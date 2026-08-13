namespace TelemetryProxy.Models;

// То, что уходит на savt-backend в POST /webhooks/telemetry — см. TelemetryWebhookIn
// в app/schemas/telemetry.py на стороне сервера, схема должна совпадать 1:1
public class TelemetryWebhookPayload
{
    public required string Topic { get; set; }
    public required Dictionary<int, int> Registers { get; set; }
    public DateTimeOffset? Timestamp { get; set; }
}
