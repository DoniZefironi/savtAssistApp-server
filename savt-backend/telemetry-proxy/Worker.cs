using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Options;
using MQTTnet;
using MQTTnet.Formatter;
using MQTTnet.Protocol;
using TelemetryProxy.Models;
using TelemetryProxy.Options;

namespace TelemetryProxy;

// BackgroundService — базовый класс .NET для фонового процесса, живущего всё
// время работы хоста: ExecuteAsync стартует при запуске и работает, пока
// приложение не остановят (или пока сам метод не завершится/не упадёт).
//
// У каждого ШУ — свой брокер (свой IP), общего на всех нет. Поэтому вместо
// одного статичного MQTT-подключения из конфига держим ПУЛ подключений — по
// одному на cabinet_id, — который периодически сверяется со списком из
// GET /webhooks/telemetry/targets: появился ШУ с указанным брокером — открываем
// новое подключение, пропал/поменялись данные — закрываем старое.
public class Worker(
    ILogger<Worker> logger,
    IOptions<MqttOptions> mqttOptions,
    IHttpClientFactory httpClientFactory,
    IOptions<WebhookOptions> webhookOptions
) : BackgroundService
{
    private readonly MqttOptions _mqtt = mqttOptions.Value;
    private readonly WebhookOptions _webhook = webhookOptions.Value;

    // Проверка обрыва связи — на каждой итерации (раз в 5с), а список брокеров
    // перезапрашивается реже (TargetsPollIntervalSeconds) — не долбить сервер
    // тем же вопросом, пока подключения и так живы
    private static readonly TimeSpan ReconnectCheckInterval = TimeSpan.FromSeconds(5);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var connections = new Dictionary<int, (IMqttClient Client, TelemetryTarget Target)>();
        var lastDiscovery = DateTimeOffset.MinValue;
        // Пока ни разу не получилось получить список брокеров (например api ещё не
        // успел подняться сразу после рестарта) — повторяем быстро (раз в 5с), а не
        // ждём полный TargetsPollIntervalSeconds (по умолч. 60с) до следующей попытки
        var everSucceeded = false;

        try
        {
            while (!stoppingToken.IsCancellationRequested)
            {
                var pollInterval = everSucceeded
                    ? TimeSpan.FromSeconds(_webhook.TargetsPollIntervalSeconds)
                    : ReconnectCheckInterval;

                if (DateTimeOffset.UtcNow - lastDiscovery >= pollInterval)
                {
                    if (await RefreshTargetsAsync(connections, stoppingToken))
                    {
                        everSucceeded = true;
                    }
                    lastDiscovery = DateTimeOffset.UtcNow;
                }

                await ReconnectStaleAsync(connections, stoppingToken);

                await Task.Delay(ReconnectCheckInterval, stoppingToken);
            }
        }
        finally
        {
            foreach (var (client, _) in connections.Values)
            {
                if (client.IsConnected)
                {
                    await client.DisconnectAsync(cancellationToken: CancellationToken.None);
                }
                client.Dispose();
            }
        }
    }

    // Сверяет актуальный список брокеров с уже открытыми подключениями:
    // закрывает лишние/изменившиеся, открывает новые. Возвращает false, если
    // список вообще не удалось получить (api недоступен) — ExecuteAsync тогда
    // повторяет попытку быстрее, а не ждёт полный интервал опроса
    private async Task<bool> RefreshTargetsAsync(
        Dictionary<int, (IMqttClient Client, TelemetryTarget Target)> connections, CancellationToken stoppingToken
    )
    {
        List<TelemetryTarget> targets;
        try
        {
            targets = await FetchTargetsAsync(stoppingToken);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Не удалось получить список брокеров с сервера");
            return false;
        }

        var targetsById = targets.ToDictionary(t => t.CabinetId);

        // закрыть то, чего больше нет в списке, или у чего сменился брокер/топик/логин
        foreach (var cabinetId in connections.Keys.ToList())
        {
            var (client, oldTarget) = connections[cabinetId];
            var stillValid = targetsById.TryGetValue(cabinetId, out var current) && TargetEquals(current, oldTarget);
            if (stillValid)
            {
                continue;
            }

            logger.LogInformation(
                "Отключаюсь от ШУ {CabinetId} ({Host}:{Port}) — конфиг изменился или ШУ убран",
                cabinetId, oldTarget.Host, oldTarget.Port
            );
            if (client.IsConnected)
            {
                await client.DisconnectAsync(cancellationToken: CancellationToken.None);
            }
            client.Dispose();
            connections.Remove(cabinetId);
        }

        // открыть то, чего ещё нет
        foreach (var target in targets)
        {
            if (connections.ContainsKey(target.CabinetId))
            {
                continue;
            }
            await ConnectAsync(connections, target, stoppingToken);
        }

        return true;
    }

    private static bool TargetEquals(TelemetryTarget a, TelemetryTarget b) =>
        a.Host == b.Host && a.Port == b.Port && a.Topic == b.Topic
        && a.Username == b.Username && a.Password == b.Password;

    // Переподключение к тем брокерам, у которых связь оборвалась, но конфиг
    // не менялся — не трогаем список целиком, только то, что реально отвалилось
    private async Task ReconnectStaleAsync(
        Dictionary<int, (IMqttClient Client, TelemetryTarget Target)> connections, CancellationToken stoppingToken
    )
    {
        foreach (var (cabinetId, (client, target)) in connections.ToList())
        {
            if (client.IsConnected)
            {
                continue;
            }
            try
            {
                logger.LogInformation(
                    "Переподключаюсь к ШУ {CabinetId} ({Host}:{Port})...", cabinetId, target.Host, target.Port
                );
                var connectResult = await client.ConnectAsync(BuildClientOptions(target), stoppingToken);
                if (connectResult.ResultCode != MqttClientConnectResultCode.Success)
                {
                    // ConnectAsync НЕ бросает исключение на отказ брокера (CONNACK с
                    // ошибкой) — только возвращает результат с кодом, это надо проверять
                    // явно, иначе следующий SubscribeAsync упадёт с невнятным
                    // "MqttClientNotConnectedException" без объяснения, ПОЧЕМУ
                    logger.LogError(
                        "Брокер отклонил подключение для ШУ {CabinetId} ({Host}:{Port}): {ResultCode} {Reason}",
                        cabinetId, target.Host, target.Port, connectResult.ResultCode, connectResult.ReasonString
                    );
                    continue;
                }
                await client.SubscribeAsync(
                    target.Topic, MqttQualityOfServiceLevel.AtLeastOnce, cancellationToken: stoppingToken
                );
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Не удалось подключиться к ШУ {CabinetId} ({Host}:{Port})", cabinetId, target.Host, target.Port);
            }
        }
    }

    private async Task ConnectAsync(
        Dictionary<int, (IMqttClient Client, TelemetryTarget Target)> connections,
        TelemetryTarget target, CancellationToken stoppingToken
    )
    {
        var client = new MqttClientFactory().CreateMqttClient();
        client.ApplicationMessageReceivedAsync += HandleMessageAsync;

        try
        {
            var connectResult = await client.ConnectAsync(BuildClientOptions(target), stoppingToken);
            if (connectResult.ResultCode != MqttClientConnectResultCode.Success)
            {
                // ConnectAsync НЕ бросает исключение на отказ брокера (CONNACK с
                // ошибкой) — только возвращает результат с кодом, это надо проверять
                // явно, иначе следующий SubscribeAsync упадёт с невнятным
                // "MqttClientNotConnectedException" без объяснения, ПОЧЕМУ
                logger.LogError(
                    "Брокер отклонил подключение для ШУ {CabinetId} ({Host}:{Port}): {ResultCode} {Reason}",
                    target.CabinetId, target.Host, target.Port, connectResult.ResultCode, connectResult.ReasonString
                );
                client.Dispose();
                return;
            }
            // AtLeastOnce (QoS 1) — с дефолтным QoS 0 брокер не гарантирует доставку
            // при разрыве связи, авария могла бы молча потеряться в самый нужный момент
            await client.SubscribeAsync(
                target.Topic, MqttQualityOfServiceLevel.AtLeastOnce, cancellationToken: stoppingToken
            );
            connections[target.CabinetId] = (client, target);
            logger.LogInformation(
                "Подключился к ШУ {CabinetId}: {Host}:{Port}, топик {Topic}",
                target.CabinetId, target.Host, target.Port, target.Topic
            );
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Не удалось подключиться к ШУ {CabinetId} ({Host}:{Port})", target.CabinetId, target.Host, target.Port);
            client.Dispose();
        }
    }

    private MqttClientOptions BuildClientOptions(TelemetryTarget target)
    {
        var builder = new MqttClientOptionsBuilder()
            .WithTcpServer(target.Host, target.Port)
            .WithClientId($"{_mqtt.ClientIdPrefix}-{target.CabinetId}")
            // MQTTnet по умолчанию запрашивает MQTT v5 — большинство промышленных
            // брокеров (обычный Mosquitto без спец. настройки) понимают только
            // v3.1.1 и отвечают отказом на CONNECT с v5 (код 1 — "unacceptable
            // protocol version" из мира v3.1.1, MQTTnet его никак не переводит)
            .WithProtocolVersion(MqttProtocolVersion.V311);

        if (!string.IsNullOrEmpty(target.Username))
        {
            builder = builder.WithCredentials(target.Username, target.Password);
        }

        return builder.Build();
    }

    private async Task<List<TelemetryTarget>> FetchTargetsAsync(CancellationToken stoppingToken)
    {
        using var client = httpClientFactory.CreateClient(nameof(Worker));
        using var request = new HttpRequestMessage(HttpMethod.Get, $"{_webhook.BaseUrl}/webhooks/telemetry/targets");
        request.Headers.Add("X-Telemetry-Secret", _webhook.Secret);

        using var response = await client.SendAsync(request, stoppingToken);

        var targets = await response.Content.ReadFromJsonAsync<List<TelemetryTarget>>(cancellationToken: stoppingToken);
        return targets ?? [];
    }

    // Один вызов на каждое входящее MQTT-сообщение. Формат самого контроллера —
    // [{"500": 9}, {"501": 5732}, ...] (массив одноключевых объектов, особенность
    // прошивки, не наша). Реассемблировать несколько таких сообщений в одну
    // "развёртку" не нужно — вебхук на сервере принимает любое кол-во регистров
    // за раз, поэтому каждое MQTT-сообщение пересылается сразу, как пришло.
    private async Task HandleMessageAsync(MqttApplicationMessageReceivedEventArgs e)
    {
        var topic = e.ApplicationMessage.Topic;
        try
        {
            // Контроллер публикует из буфера фиксированного размера — если реальный
            // текст короче буфера, остаток забит нулевыми байтами. TrimEnd('\0')
            // убирает их перед парсингом, иначе JSON-парсер падает на "мусоре"
            // сразу после закрывающей ']'
            var raw = e.ApplicationMessage.ConvertPayloadToString()?.TrimEnd('\0');
            if (string.IsNullOrEmpty(raw))
            {
                throw new JsonException("Пустой payload");
            }
            var entries = JsonSerializer.Deserialize<List<Dictionary<int, int>>>(raw)
                ?? throw new JsonException("Пустой payload");

            var registers = new Dictionary<int, int>();
            foreach (var entry in entries)
            {
                foreach (var (address, value) in entry)
                {
                    registers[address] = value;
                }
            }

            await SendToWebhookAsync(topic, registers);
        }
        catch (Exception ex)
        {
            // Одно плохое сообщение не должно ронять весь цикл подписки —
            // остальные топики/контроллеры должны продолжать доставляться
            logger.LogError(ex, "Не удалось обработать сообщение из топика {Topic}", topic);
        }
    }

    // Транзиентные сбои (сеть моргнула, api как раз передеплоился — 5xx) стоит
    // повторить: несколько секунд простоя api не должны тихо стирать аварию.
    // Постоянные ошибки (4xx — неизвестный топик, неверный секрет) повторами
    // не лечатся, только откладывают появление в логах — на них не ретраим.
    private const int MaxWebhookAttempts = 3;

    private async Task SendToWebhookAsync(string topic, Dictionary<int, int> registers)
    {
        var payload = new TelemetryWebhookPayload
        {
            Topic = topic,
            Registers = registers,
            Timestamp = DateTimeOffset.UtcNow,
        };

        var url = $"{_webhook.BaseUrl}/webhooks/telemetry";

        for (var attempt = 1; attempt <= MaxWebhookAttempts; attempt++)
        {
            try
            {
                using var client = httpClientFactory.CreateClient(nameof(Worker));
                using var request = new HttpRequestMessage(HttpMethod.Post, url)
                {
                    Content = JsonContent.Create(payload),
                };
                request.Headers.Add("X-Telemetry-Secret", _webhook.Secret);

                using var response = await client.SendAsync(request);
                if (response.IsSuccessStatusCode)
                {
                    return;
                }

                var body = await response.Content.ReadAsStringAsync();
                logger.LogWarning(
                    "Вебхук вернул {Status} для топика {Topic} (попытка {Attempt}/{Max}): {Body}",
                    response.StatusCode, topic, attempt, MaxWebhookAttempts, body
                );

                if ((int)response.StatusCode < 500)
                {
                    return; // не транзиентная ошибка (4xx) — повторять бессмысленно
                }
            }
            catch (Exception ex)
            {
                logger.LogWarning(
                    ex, "Не удалось отправить вебхук для топика {Topic} (попытка {Attempt}/{Max})",
                    topic, attempt, MaxWebhookAttempts
                );
            }

            if (attempt < MaxWebhookAttempts)
            {
                await Task.Delay(TimeSpan.FromSeconds(2 * attempt));
            }
        }

        logger.LogError(
            "Сообщение из топика {Topic} потеряно — вебхук недоступен после {Max} попыток",
            topic, MaxWebhookAttempts
        );
    }
}
