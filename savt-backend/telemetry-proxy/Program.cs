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
// (TELEMETRY_WEBHOOK_SECRET и т.п.) привела бы не к понятной ошибке при старте,
// а к бесконечному циклу "не могу получить список брокеров" в логах.
// Про Mqtt:Host/Port валидировать здесь уже нечего — они больше не тут, а в
// GET /webhooks/telemetry/targets, свои у каждого ШУ (см. Options/MqttOptions.cs)
var webhookOptions = host.Services.GetRequiredService<IOptions<WebhookOptions>>().Value;

if (string.IsNullOrWhiteSpace(webhookOptions.BaseUrl))
{
    throw new InvalidOperationException("Webhook:BaseUrl (переменная окружения Webhook__BaseUrl) не задан");
}
if (string.IsNullOrWhiteSpace(webhookOptions.Secret))
{
    throw new InvalidOperationException("Webhook:Secret (переменная окружения Webhook__Secret / TELEMETRY_WEBHOOK_SECRET) не задан");
}

host.Run();
