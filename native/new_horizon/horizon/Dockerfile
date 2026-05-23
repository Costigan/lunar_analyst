# syntax=docker/dockerfile:1

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

COPY native/new_horizon/moonlib/moonlib.csproj native/new_horizon/moonlib/
COPY native/new_horizon/horizon/horizon.csproj native/new_horizon/horizon/
RUN dotnet restore native/new_horizon/horizon/horizon.csproj

# moonlib.csproj includes ../../third_party/cspice/linux-x64/libcspice.so
# so this path must exist in the build context before publish.
COPY native/third_party/cspice/linux-x64/libcspice.so native/third_party/cspice/linux-x64/libcspice.so
COPY native/new_horizon/ native/new_horizon/
RUN dotnet publish native/new_horizon/horizon/horizon.csproj -c Release -o /out

FROM mcr.microsoft.com/dotnet/runtime:10.0 AS runtime
WORKDIR /app

COPY --from=build /out/ ./

ENTRYPOINT ["./horizon"]
