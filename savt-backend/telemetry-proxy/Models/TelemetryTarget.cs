using System.Text.Json.Serialization;

namespace TelemetryProxy.Models;

// Один брокер, к которому нужно быть подключённым — из GET /webhooks/telemetry/targets.
// Совпадает по полям с TelemetryTargetOut на стороне сервера (app/schemas/telemetry.py).
//
// Явные [JsonPropertyName] обязательны: сервер отдаёт snake_case ("cabinet_id"),
// а System.Text.Json по умолчанию не сопоставляет его с PascalCase "CabinetId"
// даже без учёта регистра — это не просто разный регистр, а разное имя.
// Без атрибутов Deserialize кидал бы исключение на required-свойствах.
public class TelemetryTarget
{
    [JsonPropertyName("cabinet_id")]
    public required int CabinetId { get; set; }

    [JsonPropertyName("host")]
    public required string Host { get; set; }

    [JsonPropertyName("port")]
    public required int Port { get; set; }

    [JsonPropertyName("topic")]
    public required string Topic { get; set; }

    [JsonPropertyName("username")]
    public string? Username { get; set; }

    [JsonPropertyName("password")]
    public string? Password { get; set; }
}
