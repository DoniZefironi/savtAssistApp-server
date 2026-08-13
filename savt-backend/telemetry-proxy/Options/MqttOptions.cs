namespace TelemetryProxy.Options;

// Секция "Mqtt" в appsettings.json / переменных окружения (Mqtt__ClientIdPrefix)
// Host/Port/Username/Password сюда больше НЕ входят — они разные у каждого ШУ
// (свой брокер на каждый контроллер), берутся динамически из
// GET /webhooks/telemetry/targets, а не из статичного конфига этого сервиса.
public class MqttOptions
{
    public const string SectionName = "Mqtt";

    // К нему добавляется "-{cabinet_id}" на каждое подключение — чтобы у
    // разных брокеров не совпадали ClientId, если вдруг несколько экземпляров
    // прокси когда-нибудь будут смотреть в один и тот же брокер
    public string ClientIdPrefix { get; set; } = "savt-telemetry-proxy";
}
