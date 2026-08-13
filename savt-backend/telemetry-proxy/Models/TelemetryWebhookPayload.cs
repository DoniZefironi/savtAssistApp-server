using System.Text.Json.Serialization;

namespace TelemetryProxy.Models;

// То, что уходит на savt-backend в POST /webhooks/telemetry — см. TelemetryWebhookIn
// в app/schemas/telemetry.py на стороне сервера, схема должна совпадать 1:1.
//
// System.Text.Json по умолчанию сериализует PascalCase-имена свойств как есть
// ("Topic", не "topic") — без явных [JsonPropertyName] сервер отвечал бы 422
// на каждый вызов, потому что Pydantic ждёт точное совпадение имени поля.
public class TelemetryWebhookPayload
{
    [JsonPropertyName("topic")]
    public required string Topic { get; set; }

    [JsonPropertyName("registers")]
    public required Dictionary<int, int> Registers { get; set; }

    [JsonPropertyName("timestamp")]
    public DateTimeOffset? Timestamp { get; set; }
}
