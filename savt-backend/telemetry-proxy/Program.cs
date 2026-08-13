using Microsoft.Extensions.Options;
using TelemetryProxy;
using TelemetryProxy.Options;

var builder = Host.CreateApplicationBuilder(args);

// Биндинг секций appsettings.json на строго типизированные классы — IOptions<T>
// потом просто внедряется в конструктор (см. Worker.cs), без ручного чтения строк
builder.Services.Configure<MqttOptions>(builder.Configuration.GetSection(MqttOptions.SectionName));
builder.Services.Configure<WebhookOptions>(builder.Configuration.GetSection(WebhookOptions.SectionName));

// Именованный HttpClient через фабрику — так его жизненным циклом (пул
// соединений, DNS-переоткрытие) управляет сам .NET, а не мы вручную
builder.Services.AddHttpClient(nameof(Worker));

builder.Services.AddHostedService<Worker>();

var host = builder.Build();

// Fail-fast при незаполненном конфиге — без этого забытая переменная окружения
// (TELEMETRY_MQTT_HOST/TELEMETRY_WEBHOOK_SECRET) привела бы не к понятной ошибке
// при старте, а к бесконечному циклу реконнекта с общим Exception в логах
var mqttOptions = host.Services.GetRequiredService<IOptions<MqttOptions>>().Value;
var webhookOptions = host.Services.GetRequiredService<IOptions<WebhookOptions>>().Value;

if (string.IsNullOrWhiteSpace(mqttOptions.Host))
{
    throw new InvalidOperationException("Mqtt:Host (переменная окружения Mqtt__Host / TELEMETRY_MQTT_HOST) не задан");
}
if (string.IsNullOrWhiteSpace(webhookOptions.Url))
{
    throw new InvalidOperationException("Webhook:Url (переменная окружения Webhook__Url) не задан");
}
if (string.IsNullOrWhiteSpace(webhookOptions.Secret))
{
    throw new InvalidOperationException("Webhook:Secret (переменная окружения Webhook__Secret / TELEMETRY_WEBHOOK_SECRET) не задан");
}

host.Run();
