namespace TelemetryProxy.Options;

// Секция "Mqtt" в appsettings.json / переменных окружения (Mqtt__Host и т.п.)
public class MqttOptions
{
    public const string SectionName = "Mqtt";

    public string Host { get; set; } = "";
    public int Port { get; set; } = 1883;

    // "#" — MQTT-wildcard "вообще всё, что есть на брокере", любой топик любой
    // вложенности одной подпиской. Не привязываемся к конкретному формату имени
    // топика (вроде "26_001/1/data") — реальные топики контроллеров не всегда
    // будут ему следовать (см. тестовый LicOS с плоским "LicOS_PUBM"). Соответствие
    // "топик → ШУ" всё равно решается только на бэкенде, точным совпадением с
    // Cabinet.mqtt_topic — этот фильтр лишь про то, что вообще долетает до прокси.
    public string TopicFilter { get; set; } = "#";

    public string ClientId { get; set; } = "savt-telemetry-proxy";
    public string? Username { get; set; }
    public string? Password { get; set; }
}
