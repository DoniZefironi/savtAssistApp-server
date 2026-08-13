namespace TelemetryProxy.Options;

// Секция "Webhook" в appsettings.json — куда и с каким секретом слать
// расшифрованные (в смысле формата, не шифрования) сообщения на savt-backend
public class WebhookOptions
{
    public const string SectionName = "Webhook";

    // Внутри docker-compose — по имени сервиса ("http://api:8000/webhooks/telemetry"),
    // без похода через nginx и наружу вообще
    public string Url { get; set; } = "";
    public string Secret { get; set; } = "";
}
