# pihole

![Version: 1.3.0](https://img.shields.io/badge/Version-1.3.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 2026.04.1](https://img.shields.io/badge/AppVersion-2026.04.1-informational?style=flat-square)

Pi-hole v6 Helm Chart with full FTL configuration support.

**Homepage:** <https://nunoferna.github.io/pihole-helm/>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| nunoferna | <nuno.o.fernandes@outlook.pt> | <https://github.com/nunoferna> |

## Source Code

* <https://github.com/nunoferna/pihole-helm>

## Requirements

Kubernetes: `>=1.23.0`

## Examples

The [`examples/`](https://github.com/nunoferna/pihole-helm/tree/main/examples) directory contains ready-to-use values files for common deployment scenarios:

| File | Description |
|------|-------------|
| [`values-external-secrets.yaml`](https://github.com/nunoferna/pihole-helm/blob/main/examples/values-external-secrets.yaml) | Integrate with [External Secrets Operator](https://external-secrets.io) to pull Pi-hole credentials from an external provider (Vault, AWS, GCP, Azure, etc.) |
| [`values-monitoring.yaml`](https://github.com/nunoferna/pihole-helm/blob/main/examples/values-monitoring.yaml) | Enable the full monitoring stack: Pi-hole Exporter sidecar, Prometheus `ServiceMonitor`, and a Grafana dashboard `ConfigMap` |
| [`values-vault.yaml`](https://github.com/nunoferna/pihole-helm/blob/main/examples/values-vault.yaml) | Mount Pi-hole secrets from [HashiCorp Vault](https://www.vaultproject.io) using the Vault Agent Injector sidecar |

To use an example, pass it alongside your own overrides:

```sh
helm install pihole nunoferna/pihole \
  -f examples/values-monitoring.yaml \
  -f my-values.yaml
```

## Values

### Metrics

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| metrics.dashboards.enabled | boolean | `false` | Whether to create Grafana dashboard resources for the Pi-hole metrics exporter. Requires metrics.service.enabled to be true. |
| metrics.enabled | boolean | `false` | Whether to enable the Pi-hole metrics exporter. Provides Prometheus metrics on port 9617. Required for any metrics-related features (ServiceMonitor, Grafana dashboards, etc.). |
| metrics.env | array | `[{"name":"INTERVAL","value":"10s"}]` | Additional environment variables for the metrics exporter. PIHOLE_HOSTNAME, PIHOLE_PORT, and PIHOLE_PROTOCOL are auto-generated from the number of replicas (headless DNS for StatefulSet, ClusterIP FQDN for Deployment). Use this to pass PIHOLE_PASSWORD, INTERVAL, or other custom variables. |
| metrics.image.repository | string | `"ekofr/pihole-exporter"` | Container image for the Pi-hole metrics exporter. |
| metrics.port | integer | `9617` | Port on which the metrics exporter will listen. |
| metrics.serviceMonitor.enabled | boolean | `false` | Whether to create a ServiceMonitor resource for Prometheus Operator integration. |

### K8S Services

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| metrics.service.enabled | boolean | `true` | Metrics Kubernetes Service. Required for external access to metrics endpoint. (port 9617) |
| pihole.ftl.dhcp.ipv6.service.enabled | boolean | false | DHCPv6 Kubernetes Service. Required for external access to DHCPv6 functionality (port 547). |
| pihole.ftl.dhcp.service.enabled | boolean | false | DHCP Kubernetes Service. Required for external access to DHCP functionality (port 67). |
| pihole.ftl.dns.service.enabled | boolean | true | DNS Kubernetes Service. Required for external access to DNS functionality (port 53). |
| pihole.ftl.dns.service.splitTcpUdp | boolean | false | Whether to create separate Kubernetes Services for TCP and UDP DNS ports. |
| pihole.ftl.ntp.service.enabled | boolean | false | NTP Kubernetes Service. Required for external access to NTP functionality (port 123). |
| pihole.web.service.enabled | boolean | `true` | Web Kubernetes Service. Required for external access to the Pi-hole web interface. (port 80/443) |

### Clients

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.clients | array | [] | List of Pi-hole clients. Each client can be identified by an IPv4 address, IPv6 address, or MAC address. |

### Database

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.ftl.database.DBimport | boolean | true | Should FTL load information from the database on startup to be aware of the most recent history? |
| pihole.ftl.database.DBinterval | integer | 60 | Database update interval in seconds. |
| pihole.ftl.database.maxDBdays | integer | 91 | Maximum number of days to keep database entries. |
| pihole.ftl.database.network.expire | integer | 91 | Number of days after which an entry in the ARP cache is considered stale. |
| pihole.ftl.database.network.parseARPcache | boolean | true | Whether to parse the ARP cache. |
| pihole.ftl.database.useWAL | boolean | true | Whether to use WAL (Write-Ahead Logging) for the database. |

### DHCP

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.ftl.dhcp.active | boolean | false | No description provided. |
| pihole.ftl.dhcp.end | string | "" | No description provided. |
| pihole.ftl.dhcp.hosts | array | [] | No description provided. |
| pihole.ftl.dhcp.ignoreUnknownClients | boolean | false | No description provided. |
| pihole.ftl.dhcp.ipv6.active | boolean | false | DHCPv6 functionality. |
| pihole.ftl.dhcp.leaseTime | string | "" | No description provided. |
| pihole.ftl.dhcp.logging | boolean | false | No description provided. |
| pihole.ftl.dhcp.multiDNS | boolean | false | No description provided. |
| pihole.ftl.dhcp.netmask | string | "" | No description provided. |
| pihole.ftl.dhcp.rapidCommit | boolean | false | No description provided. |
| pihole.ftl.dhcp.router | string | "" | No description provided. |
| pihole.ftl.dhcp.start | string | "" | No description provided. |

### DNS

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.ftl.dns.CNAMEdeepInspect | boolean | true | No description provided. |
| pihole.ftl.dns.EDNS0ECS | boolean | true | No description provided. |
| pihole.ftl.dns.analyzeOnlyAandAAAA | boolean | false | No description provided. |
| pihole.ftl.dns.blockESNI | boolean | true | No description provided. |
| pihole.ftl.dns.blockTTL | integer | 2 | No description provided. |
| pihole.ftl.dns.blocking.active | boolean | true | No description provided. |
| pihole.ftl.dns.blocking.edns | string | "TEXT" | Options: https://docs.pi-hole.net/ftldns/configfile/#edns |
| pihole.ftl.dns.blocking.mode | string | "NULL" | Options: https://docs.pi-hole.net/ftldns/configfile/#mode |
| pihole.ftl.dns.bogusPriv | boolean | true | No description provided. |
| pihole.ftl.dns.cache.optimizer | integer | 3600 | No description provided. |
| pihole.ftl.dns.cache.size | integer | 10000 | No description provided. |
| pihole.ftl.dns.cache.upstreamBlockedTTL | integer | 86400 | No description provided. |
| pihole.ftl.dns.cnameRecords | array | [] | No description provided. |
| pihole.ftl.dns.dnssec | boolean | false | No description provided. |
| pihole.ftl.dns.domain.local | boolean | true | No description provided. |
| pihole.ftl.dns.domain.name | string | "lan" | No description provided. |
| pihole.ftl.dns.domainNeeded | boolean | false | No description provided. |
| pihole.ftl.dns.expandHosts | boolean | false | No description provided. |
| pihole.ftl.dns.hostRecord | string | "" | No description provided. |
| pihole.ftl.dns.hosts | array | [] | No description provided. |
| pihole.ftl.dns.ignoreLocalhost | boolean | false | No description provided. |
| pihole.ftl.dns.interface | string | "" | No description provided. |
| pihole.ftl.dns.listeningMode | string | "LOCAL" | Options: https://docs.pi-hole.net/ftldns/configfile/#listeningmode |
| pihole.ftl.dns.localise | boolean | true | No description provided. |
| pihole.ftl.dns.piholePTR | string | "PI.HOLE" | Options: https://docs.pi-hole.net/ftldns/configfile/#piholeptr |
| pihole.ftl.dns.port | integer | 53 | No description provided. |
| pihole.ftl.dns.queryLogging | boolean | true | No description provided. |
| pihole.ftl.dns.rateLimit.count | integer | 1000 | No description provided. |
| pihole.ftl.dns.rateLimit.interval | integer | 60 | No description provided. |
| pihole.ftl.dns.reply.blocking.IPv4 | string | "" | No description provided. |
| pihole.ftl.dns.reply.blocking.IPv6 | string | "" | No description provided. |
| pihole.ftl.dns.reply.blocking.force4 | boolean | false | No description provided. |
| pihole.ftl.dns.reply.blocking.force6 | boolean | false | No description provided. |
| pihole.ftl.dns.reply.host.IPv4 | string | "" | No description provided. |
| pihole.ftl.dns.reply.host.IPv6 | string | "" | No description provided. |
| pihole.ftl.dns.reply.host.force4 | boolean | false | No description provided. |
| pihole.ftl.dns.reply.host.force6 | boolean | false | No description provided. |
| pihole.ftl.dns.replyWhenBusy | string | "ALLOW" | Options: https://docs.pi-hole.net/ftldns/configfile/#replywhenbusy |
| pihole.ftl.dns.revServers | array | [] | No description provided. |
| pihole.ftl.dns.showDNSSEC | boolean | true | No description provided. |
| pihole.ftl.dns.specialDomains.designatedResolver | boolean | true | No description provided. |
| pihole.ftl.dns.specialDomains.iCloudPrivateRelay | boolean | true | No description provided. |
| pihole.ftl.dns.specialDomains.mozillaCanary | boolean | true | No description provided. |
| pihole.ftl.dns.upstreams | array | [8.8.8.8, 8.8.4.4] | List of upstream DNS servers. |

### Advanced

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.ftl.files.database | string | "/etc/pihole/pihole-FTL.db" | No description provided. |
| pihole.ftl.files.gravity | string | "/etc/pihole/gravity.db" | No description provided. |
| pihole.ftl.files.gravity_tmp | string | "/tmp" | No description provided. |
| pihole.ftl.files.log.dnsmasq | string | "/var/log/pihole/pihole.log" | No description provided. |
| pihole.ftl.files.log.ftl | string | "/var/log/pihole/FTL.log" | No description provided. |
| pihole.ftl.files.log.webserver | string | "/var/log/pihole/webserver.log" | No description provided. |
| pihole.ftl.files.macvendor | string | "/etc/pihole/macvendor.db" | No description provided. |
| pihole.ftl.files.pcap | string | "" | No description provided. |
| pihole.ftl.files.pid | string | "/run/pihole-FTL.pid" | No description provided. |
| pihole.ftl.misc.addr2line | boolean | true | No description provided. |
| pihole.ftl.misc.check.disk | integer | 90 | No description provided. |
| pihole.ftl.misc.check.load | boolean | true | No description provided. |
| pihole.ftl.misc.check.shmem | integer | 90 | No description provided. |
| pihole.ftl.misc.delay_startup | integer | 0 | No description provided. |
| pihole.ftl.misc.dnsmasq_lines | array | [] | No description provided. |
| pihole.ftl.misc.etc_dnsmasq_d | boolean | false | No description provided. |
| pihole.ftl.misc.extraLogging | boolean | false | No description provided. |
| pihole.ftl.misc.hide_dnsmasq_warn | boolean | false | No description provided. |
| pihole.ftl.misc.nice | integer | -10 | No description provided. |
| pihole.ftl.misc.normalizeCPU | boolean | true | No description provided. |
| pihole.ftl.misc.privacylevel | integer | 0 | No description provided. |
| pihole.ftl.misc.readOnly | boolean | false | No description provided. |

### NTP

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.ftl.ntp.ipv4.active | boolean | true | NTP functionality on IPv4. |
| pihole.ftl.ntp.ipv4.address | string | "" | NTP address on IPv4. |
| pihole.ftl.ntp.ipv6.active | boolean | true | NTP functionality on IPv6. |
| pihole.ftl.ntp.ipv6.address | string | "" | NTP address on IPv6. |
| pihole.ftl.ntp.sync.active | boolean | true | Whether to enable NTP time synchronization. |
| pihole.ftl.ntp.sync.count | integer | 8 | NTP sync count. |
| pihole.ftl.ntp.sync.interval | integer | 3600 | NTP sync interval in seconds. |
| pihole.ftl.ntp.sync.rtc.device | string | "/dev/rtc0" | RTC device to use when setting the clock. |
| pihole.ftl.ntp.sync.rtc.set | boolean | false | Whether to set the RTC (Real-Time Clock) to the current time. |
| pihole.ftl.ntp.sync.rtc.utc | boolean | true | Whether to use UTC time when setting the RTC. |
| pihole.ftl.ntp.sync.server | string | "pool.ntp.org" | NTP server to sync with. Can be an IP address or hostname. Set to empty string to disable syncing. |

### Webserver

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.ftl.webserver.acl | string | "" | No description provided. |
| pihole.ftl.webserver.advancedOpts | array | [] | Additional options to be passed to the webserver. Can be used to set custom options like TLS configuration, etc. |
| pihole.ftl.webserver.api.allow_destructive | boolean | true | No description provided. |
| pihole.ftl.webserver.api.app_pwhash | string | "" | No description provided. |
| pihole.ftl.webserver.api.app_sudo | boolean | false | No description provided. |
| pihole.ftl.webserver.api.cli_pw | boolean | true | No description provided. |
| pihole.ftl.webserver.api.client_history_global_max | boolean | true | No description provided. |
| pihole.ftl.webserver.api.excludeClients | array | [] | No description provided. |
| pihole.ftl.webserver.api.excludeDomains | array | [] | No description provided. |
| pihole.ftl.webserver.api.maxClients | integer | 10 | No description provided. |
| pihole.ftl.webserver.api.maxHistory | integer | 86400 | No description provided. |
| pihole.ftl.webserver.api.max_sessions | integer | 16 | No description provided. |
| pihole.ftl.webserver.api.prettyJSON | boolean | false | No description provided. |
| pihole.ftl.webserver.api.pwhash | string | "" | No description provided. |
| pihole.ftl.webserver.api.temp.limit | number | 60.000000 | No description provided. |
| pihole.ftl.webserver.api.temp.unit | string | "C" | Options: https://docs.pi-hole.net/ftldns/configfile/#unit |
| pihole.ftl.webserver.api.totp_secret | string | "" | No description provided. |
| pihole.ftl.webserver.domain | string | "pi.hole" | Domain to use for the Pi-hole web interface. Set to empty string to disable. |
| pihole.ftl.webserver.interface.boxed | boolean | true | Should the web interface use the boxed layout? |
| pihole.ftl.webserver.interface.theme | string | "default-auto" | Theme used by the Pi-hole web interface. Options: https://docs.pi-hole.net/ftldns/configfile/#theme |
| pihole.ftl.webserver.paths.prefix | string | "" | URL prefix for the Pi-hole API. Set to empty string for no prefix (root). |
| pihole.ftl.webserver.paths.webhome | string | "/admin/" | URL path prefix for the Pi-hole web interface. Set to empty string for no prefix (root). |
| pihole.ftl.webserver.paths.webroot | string | "/var/www/html" | Web root directory for the Pi-hole web interface. |
| pihole.ftl.webserver.port | string | "80o,443os,[::]:80o,[::]:443os" | No description provided. |
| pihole.ftl.webserver.serve_all | boolean | false | No description provided. |
| pihole.ftl.webserver.session.restore | boolean | true | Whether to restore sessions after a restart. If false, all sessions will be lost on restart. |
| pihole.ftl.webserver.session.timeout | integer | 1800 | Session timeout in seconds. |
| pihole.ftl.webserver.threads | integer | 50 | No description provided. |
| pihole.ftl.webserver.tls.cert | string | "/etc/pihole/tls.pem" | Path to TLS certificate file. Set to empty string to disable TLS. |
| pihole.ftl.webserver.tls.validity | integer | 47 | TLS validity period in days. |

### Groups

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.groups | array | [] | List of Pi-hole groups. Groups are used to apply settings (e.g. adlists) to specific clients. |
| pihole.groups[0].description | string | `"Default group"` | Description of the group. |
| pihole.groups[0].enabled | boolean | `true` | Whether the group is enabled. |

### Web

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.web.existingSecret | string | `""` | Name of an existing Kubernetes Secret in the same namespace that contains the web interface password. If set, this secret will be used instead of creating a new one. Ignored if using external secret injection. |
| pihole.web.password | string | `""` | Web interface password. Set to empty string to disable password protection. Ignored if using external secret injection. |
| pihole.web.passwordSecretKey | string | `"password"` | Key in the Kubernetes secret that contains the web interface password. Ignored if using external secret injection. |
| pihole.web.skipPasswordEnv | boolean | `false` | Whether to skip setting the default web password environment variable when using external secret injection. Set to true if you are defining the FTLCONF_webserver_api_password variable in extraEnv with a value sourced from an external secret. |

### Other Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` |  |
| args | list | `[]` |  |
| clusterDomain | string | "cluster.local" | Kubernetes cluster domain. Override only if your cluster uses a non-default DNS domain. |
| command | list | `[]` |  |
| extraEnv | list | `[]` |  |
| extraInitContainers | list | `[]` |  |
| extraVolumes | list | `[]` |  |
| image.pullPolicy | string | `"IfNotPresent"` |  |
| image.repository | string | `"pihole/pihole"` |  |
| image.tag | string | `""` |  |
| imagePullSecrets | list | `[]` |  |
| metrics.args | list | `[]` |  |
| metrics.command | list | `[]` |  |
| metrics.dashboards.annotations | object | `{}` |  |
| metrics.dashboards.label | string | `"grafana_dashboard"` |  |
| metrics.dashboards.labelValue | string | `"1"` |  |
| metrics.image.pullPolicy | string | `"IfNotPresent"` |  |
| metrics.image.tag | string | `"v1.2.0"` |  |
| metrics.resources.limits.cpu | string | `"50m"` |  |
| metrics.resources.limits.memory | string | `"64Mi"` |  |
| metrics.resources.requests.cpu | string | `"10m"` |  |
| metrics.resources.requests.memory | string | `"32Mi"` |  |
| metrics.service.type | string | `"ClusterIP"` |  |
| metrics.serviceMonitor.annotations | object | `{}` |  |
| metrics.serviceMonitor.interval | string | `"30s"` |  |
| metrics.serviceMonitor.labels | object | `{}` |  |
| metrics.serviceMonitor.metricRelabelings | list | `[]` |  |
| metrics.serviceMonitor.relabelings | list | `[]` |  |
| metrics.serviceMonitor.scrapeTimeout | string | `"10s"` |  |
| networkPolicy.enabled | bool | `false` |  |
| networkPolicy.webAllowFrom | list | `[]` |  |
| networking.hostNetwork | bool | `false` |  |
| nodeSelector | object | `{}` |  |
| persistence.etcDnsmasq.accessMode | string | `"ReadWriteOnce"` |  |
| persistence.etcDnsmasq.enabled | bool | `false` |  |
| persistence.etcDnsmasq.size | string | `"100Mi"` |  |
| persistence.etcPihole.accessMode | string | `"ReadWriteOnce"` |  |
| persistence.etcPihole.enabled | bool | `true` |  |
| persistence.etcPihole.size | string | `"500Mi"` |  |
| pihole.additionalPackages | string | "" | Additional Alpine packages to install |
| pihole.dnsmasqUser | string | "pihole" | User that FTLDNS runs as |
| pihole.ftl.dhcp.ipv6.service.annotations | object | `{}` |  |
| pihole.ftl.dhcp.ipv6.service.externalTrafficPolicy | string | `"Local"` |  |
| pihole.ftl.dhcp.ipv6.service.ports.dhcpv6 | int | `547` |  |
| pihole.ftl.dhcp.ipv6.service.type | string | `"LoadBalancer"` |  |
| pihole.ftl.dhcp.service.annotations | object | `{}` |  |
| pihole.ftl.dhcp.service.externalTrafficPolicy | string | `"Local"` |  |
| pihole.ftl.dhcp.service.ports.dhcp | int | `67` |  |
| pihole.ftl.dhcp.service.type | string | `"LoadBalancer"` |  |
| pihole.ftl.dns.service.annotations | object | `{}` |  |
| pihole.ftl.dns.service.externalTrafficPolicy | string | `"Local"` |  |
| pihole.ftl.dns.service.ports.tcp | int | `53` |  |
| pihole.ftl.dns.service.ports.udp | int | `53` |  |
| pihole.ftl.dns.service.tcp.annotations | object | `{}` |  |
| pihole.ftl.dns.service.tcp.enabled | bool | `true` |  |
| pihole.ftl.dns.service.tcp.externalTrafficPolicy | string | `"Local"` |  |
| pihole.ftl.dns.service.tcp.port | int | `53` |  |
| pihole.ftl.dns.service.tcp.type | string | `"LoadBalancer"` |  |
| pihole.ftl.dns.service.type | string | `"LoadBalancer"` |  |
| pihole.ftl.dns.service.udp.annotations | object | `{}` |  |
| pihole.ftl.dns.service.udp.enabled | bool | `true` |  |
| pihole.ftl.dns.service.udp.externalTrafficPolicy | string | `"Local"` |  |
| pihole.ftl.dns.service.udp.port | int | `53` |  |
| pihole.ftl.dns.service.udp.type | string | `"LoadBalancer"` |  |
| pihole.ftl.ntp.service.annotations | object | `{}` |  |
| pihole.ftl.ntp.service.externalTrafficPolicy | string | `"Local"` |  |
| pihole.ftl.ntp.service.ports.ntp | int | `123` |  |
| pihole.ftl.ntp.service.type | string | `"LoadBalancer"` |  |
| pihole.ftl.resolver.networkNames | boolean | true | Control whether FTL should use the fallback option to try to obtain client names from checking the network table. |
| pihole.ftl.resolver.refreshNames | string | "IPV4_ONLY" | Options: https://docs.pi-hole.net/ftldns/configfile/#refreshnames |
| pihole.ftl.resolver.resolveIPv4 | boolean | true | Whether to resolve IPv4 addresses to hostnames in the query log and other outputs. |
| pihole.ftl.resolver.resolveIPv6 | boolean | true | Whether to resolve IPv6 addresses to hostnames in the query log and other outputs. |
| pihole.ftl.webserver.headers | array | `[]` | Headers to be set on all webserver responses. Check default value in pihole-FTL.toml for format. |
| pihole.ftlCmd | string | "no-daemon" | Customize dnsmasq startup options |
| pihole.gid | integer | 1000 | No description provided. |
| pihole.lists.adlists[0].address | string | `"https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"` |  |
| pihole.lists.adlists[0].comment | string | `"StevenBlack Master"` |  |
| pihole.lists.adlists[1].address | string | `"https://adaway.org/hosts.txt"` |  |
| pihole.lists.adlists[1].comment | string | `"AdAway"` |  |
| pihole.lists.adlists[1].enabled | bool | `false` |  |
| pihole.lists.blacklist[0].comment | string | `"Known malware"` |  |
| pihole.lists.blacklist[0].domain | string | `"bad-site.com"` |  |
| pihole.lists.regex_blacklist | list | `[]` |  |
| pihole.lists.regex_whitelist[0].comment | string | `"Allow Google subdomains"` |  |
| pihole.lists.regex_whitelist[0].domain | string | `".*\\.google\\.com$"` |  |
| pihole.lists.whitelist[0].domain | string | `"good-site.com"` |  |
| pihole.tailFtlLog | integer | 1 | Whether to output FTL log (0 or 1) |
| pihole.uid | integer | 1000 | No description provided. |
| pihole.verbose | integer | 0 | Enable verbose startup scripts (0 or 1) |
| pihole.web.service.annotations | object | `{}` |  |
| pihole.web.service.externalTrafficPolicy | string | `"Local"` |  |
| pihole.web.service.ports.http | int | `80` |  |
| pihole.web.service.ports.https | int | `443` |  |
| pihole.web.service.type | string | `"ClusterIP"` |  |
| podAnnotations | object | `{}` |  |
| podDisruptionBudget.enabled | bool | `false` |  |
| podDisruptionBudget.minAvailable | int | `1` |  |
| replicas | int | `1` |  |
| resources.limits.cpu | string | `"200m"` |  |
| resources.limits.memory | string | `"256Mi"` |  |
| resources.requests.cpu | string | `"100m"` |  |
| resources.requests.memory | string | `"128Mi"` |  |
| securityContext.capabilities.add | list | `[]` |  |
| serviceAccount.annotations | object | `{}` |  |
| serviceAccount.create | bool | `true` |  |
| serviceAccount.name | string | `"pihole"` |  |
| strategy.type | string | `"Recreate"` |  |
| timezone | string | `"UTC"` |  |
| tolerations | list | `[]` |  |
| updateStrategy.type | string | `"RollingUpdate"` |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
