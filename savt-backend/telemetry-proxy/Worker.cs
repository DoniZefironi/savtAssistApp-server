using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Options;
using MQTTnet;
using MQTTnet.Protocol;
using TelemetryProxy.Models;
using TelemetryProxy.Options;

namespace TelemetryProxy;

// BackgroundService — базовый класс .NET для фонового процесса, живущего всё
// время работы хоста: ExecuteAsync стартует при запуске и работает, пока
// приложение не остановят (или пока сам метод не завершится/не упадёт)
public class Worker(
    ILogger<Worker> logger,
    IOptions<MqttOptions> mqttOptions,
    IHttpClientFactory httpClientFactory,
    IOptions<WebhookOptions> webhookOptions
) : BackgroundService
{
    private readonly MqttOptions _mqtt = mqttOptions.Value;
    private readonly WebhookOptions _webhook = webhookOptions.Value;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var mqttClient = new MqttClientFactory().CreateMqttClient();

        var clientOptions = new MqttClientOptionsBuilder()
            .WithTcpServer(_mqtt.Host, _mqtt.Port)
            .WithClientId(_mqtt.ClientId)
            .WithCredentials(_mqtt.Username, _mqtt.Password)
            .Build();

        mqttClient.ApplicationMessageReceivedAsync += HandleMessageAsync;

        try
        {
            // Свой цикл переподключения вместо ManagedMqttClient (отдельный пакет) —
            // при разрыве просто пробуем законнектиться заново раз в 5 секунд
            while (!stoppingToken.IsCancellationRequested)
            {
                try
                {
                    if (!mqttClient.IsConnected)
                    {
                        logger.LogInformation("Подключаюсь к MQTT {Host}:{Port}...", _mqtt.Host, _mqtt.Port);
                        await mqttClient.ConnectAsync(clientOptions, stoppingToken);

                        // AtLeastOnce (QoS 1), не дефолтный AtMostOnce (QoS 0) — с QoS 0 брокер
                        // не гарантирует доставку при разрыве связи, сообщение об аварии могло бы
                        // молча потеряться именно тогда, когда оно важнее всего
                        await mqttClient.SubscribeAsync(
                            _mqtt.TopicFilter, MqttQualityOfServiceLevel.AtLeastOnce, cancellationToken: stoppingToken
                        );
                        logger.LogInformation("Подписан на {TopicFilter}", _mqtt.TopicFilter);
                    }
                }
                catch (Exception ex)
                {
                    logger.LogError(ex, "Не удалось подключиться к MQTT-брокеру, повтор через 5с");
                }

                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
            }
        }
        finally
        {
            // Аккуратное отключение при остановке хоста (docker compose stop и т.п.) —
            // CancellationToken.None: stoppingToken к этому моменту уже отменён
            if (mqttClient.IsConnected)
            {
                logger.LogInformation("Останавливаюсь, отключаюсь от MQTT...");
                await mqttClient.DisconnectAsync(cancellationToken: CancellationToken.None);
            }
        }
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
            var raw = e.ApplicationMessage.ConvertPayloadToString();
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

        for (var attempt = 1; attempt <= MaxWebhookAttempts; attempt++)
        {
            try
            {
                using var client = httpClientFactory.CreateClient(nameof(Worker));
                using var request = new HttpRequestMessage(HttpMethod.Post, _webhook.Url)
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
