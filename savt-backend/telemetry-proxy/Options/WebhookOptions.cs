namespace TelemetryProxy.Options;

// Секция "Webhook" в appsettings.json — адрес savt-backend и общий секрет.
// Используется и для приёма списка брокеров (GET .../targets), и для отправки
// самой телеметрии (POST .../webhooks/telemetry)
public class WebhookOptions
{
    public const string SectionName = "Webhook";

    // Внутри docker-compose — по имени сервиса ("http://api:8000"), без похода
    // через nginx и наружу вообще. Без хвостового "/"
    public string BaseUrl { get; set; } = "";
    public string Secret { get; set; } = "";

    // Как часто перезапрашивать список брокеров (появился новый ШУ с указанным
    // mqtt_host, у существующего изменился/пропал) — не статично при старте один раз
    public int TargetsPollIntervalSeconds { get; set; } = 60;
}
