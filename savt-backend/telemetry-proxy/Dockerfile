FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

COPY TelemetryProxy.csproj .
RUN dotnet restore

COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/runtime:10.0
WORKDIR /app
COPY --from=build /app .

# Не слушает HTTP входящих — только исходящие к MQTT-брокеру и к api,
# поэтому портов наружу не открываем
ENTRYPOINT ["dotnet", "TelemetryProxy.dll"]
