# pihole

![Version: 2.1.0](https://img.shields.io/badge/Version-2.1.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 2026.11.0](https://img.shields.io/badge/AppVersion-2026.11.0-informational?style=flat-square)

Pi-hole v6 DNS with independent replicas, verified policy snapshots and recovery tooling.

**Homepage:** <https://nunoferna.github.io/pihole-helm/>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| nunoferna | <nuno.o.fernandes@outlook.pt> | <https://github.com/nunoferna> |

## Source Code

* <https://github.com/nunoferna/pihole-helm>

## Requirements

Kubernetes: `>=1.35.0-0 <1.38.0-0`

## v2 DNS production profile

Pi-hole replicas use independent retained PVCs and stable StatefulSet identities. Read the [migration guide](MIGRATION.md) before adopting v2; v1 selectors and storage layouts require a parallel installation. Credentials must come from an existing Secret, an explicit password, or external injection. No credential is generated or printed automatically.

Internal DNS/HTTP/HTTPS ports default to 5353/8080/8443, while Services expose 53/80/443. DNS defaults to a private ClusterIP; explicitly select a load-balancer/NodePort profile and client restrictions. Kubernetes Service values live under `services.*`, separately from native `pihole.ftl` values. Load-balancer classes/source ranges and role-scoped explicit egress rules are optional. The stock image retains root startup and a writable root filesystem with reduced capabilities; Kubernetes Restricted compatibility is not claimed.

Policy reconciliation uses the local API, per-PVC ownership journals and immutable retained artifacts. It removes only owned records, protects unmanaged dependencies and gates readiness on the applied revision. For reproducible production filtering, use `policy.requirePinnedLists: true` and SHA-256 pinned HTTPS subscriptions. Bodies are verified and cached on each PVC; unchanged scheduled checks preserve serving readiness and reuse the cache. See [policy lifecycle](../../docs/policy-lifecycle.md) and [encrypted recovery](../../docs/recovery.md).

`helm test` checks every replica and the Service over UDP/TCP, plus configured filtering, authentication and metrics samples. Scheduled DNS checks and Prometheus rules are optional. External VIP probes, actual load-balancer/CNI/storage failure behavior, client source-IP preservation and alert delivery remain deployment acceptance tests. HA DNS does not coordinate DHCP leases, histories, sessions or caches.

CI gates the exact reproducible archive across the supported version/architecture matrix, blocking security checks and disposable HA/recovery fixtures. The release workflow is invoked manually after that complete run succeeds; it signs the tested bytes and resumes each distribution destination independently. See [validation evidence](../../docs/validation/README.md), [security exceptions](../../docs/security.md) and [release operations](../../docs/releasing.md).

## Examples

The [`examples/`](https://github.com/nunoferna/pihole-helm/tree/main/examples) directory contains ready-to-use values files for common deployment scenarios:

| File | Description |
|------|-------------|
| [`values-external-secrets.yaml`](https://github.com/nunoferna/pihole-helm/blob/main/examples/values-external-secrets.yaml) | Integrate with [External Secrets Operator](https://external-secrets.io) to pull Pi-hole credentials from an external provider (Vault, AWS, GCP, Azure, etc.) |
| [`values-monitoring.yaml`](https://github.com/nunoferna/pihole-helm/blob/main/examples/values-monitoring.yaml) | Enable the full monitoring stack: standalone Pi-hole Exporter, Prometheus `ServiceMonitor`, and a Grafana dashboard `ConfigMap` |
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
| metrics.image.repository | string | `"ekofr/pihole-exporter"` | Container image for the Pi-hole metrics exporter. |
| metrics.podSecurityContext | object | `{"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}}` | Pod security context for the metrics exporter. Defaults follow Pod Security Standards Restricted controls where compatible. |
| metrics.port | integer | `9617` | Port on which the metrics exporter will listen. |
| metrics.priorityClassName | string | `""` | PriorityClass for the metrics exporter pod. Separate from the DNS priorityClassName so the exporter does not inherit resolver-level priority. |
| metrics.securityContext | object | `{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true,"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}}` | Container security context for the metrics exporter. Defaults follow Pod Security Standards Restricted controls. |
| metrics.serviceMonitor.enabled | boolean | `false` | Whether to create a ServiceMonitor resource for Prometheus Operator integration. |

### K8S Services

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| metrics.service.enabled | boolean | `true` | Metrics Kubernetes Service. Required for external access to metrics endpoint. (port 9617) |
| services.dhcp.enabled | boolean | false | DHCP Kubernetes Service. Required for external access to DHCP functionality (port 67). |
| services.dhcpv6.enabled | boolean | false | DHCPv6 Kubernetes Service. Required for external access to DHCPv6 functionality (port 547). |
| services.dns.enabled | boolean | true | DNS Kubernetes Service. Required for external access to DNS functionality (port 53). |
| services.dns.splitTcpUdp | boolean | false | Whether to create separate Kubernetes Services for TCP and UDP DNS ports. |
| services.ntp.enabled | boolean | false | NTP Kubernetes Service. Required for external access to NTP functionality (port 123). |
| services.web.enabled | boolean | `true` | Web Kubernetes Service. Required for external access to the Pi-hole web interface. (port 80/443) |

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
| pihole.ftl.dns.listeningMode | string | `"ALL"` | Accept Kubernetes Service/pod traffic. Restrict DNS clients with NetworkPolicy and private service exposure. |
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

### Web

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| pihole.web.existingSecret | string | `""` | Name of an existing Kubernetes Secret in the same namespace that contains the web interface password. If set, this secret will be used instead of creating a new one. Ignored if using external secret injection. |
| pihole.web.password | string | `""` | Explicit non-empty password for a chart-managed Secret. Prefer existingSecret in production. No random password is generated. |
| pihole.web.passwordSecretKey | string | `"password"` | Key in the Kubernetes secret that contains the web interface password. Ignored if using external secret injection. |
| pihole.web.skipPasswordEnv | boolean | `false` | Whether to skip setting the default web password environment variable when using external secret injection. Set to true if you are defining the FTLCONF_webserver_api_password variable in extraEnv with a value sourced from an external secret. |

### Other Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` |  |
| args | list | `[]` |  |
| clusterDomain | string | "cluster.local" | Kubernetes cluster domain. Override only if your cluster uses a non-default DNS domain. |
| command | list | `[]` |  |
| credentialsRevision | string | `""` | Change when rotating an externally managed Secret; rolls DNS and exporter pods. |
| extraEnv | list | `[]` |  |
| extraInitContainers | list | `[]` |  |
| extraVolumeMounts | list | `[]` | Additional DNS-container mounts, for example a read-only Secret containing a TLS PEM file. |
| extraVolumes | list | `[]` |  |
| image.allowUnverified | bool | `false` | Explicit opt-out from the recorded image compatibility baseline. |
| image.digest | string | `"sha256:66c798d79a95c2691f280329c3611dba1746117c61c9f331907ca35a06a52778"` | Immutable multi-platform digest. Clear explicitly when testing another image. |
| image.pullPolicy | string | `"IfNotPresent"` |  |
| image.repository | string | `"pihole/pihole"` |  |
| image.tag | string | `""` |  |
| imagePullSecrets | list | `[]` |  |
| listeners | object | `{"dns":5353,"http":8080,"https":8443}` | Internal listener ports. Kubernetes Services retain their normal client ports. |
| metrics.affinity | object | `{}` |  |
| metrics.args | list | `[]` |  |
| metrics.command | list | `[]` |  |
| metrics.dashboards.annotations | object | `{}` |  |
| metrics.dashboards.label | string | `"grafana_dashboard"` |  |
| metrics.dashboards.labelValue | string | `"1"` |  |
| metrics.env | array | `[]` | Additional exporter variables. Scrape timing is controlled by Prometheus. Authentication uses the Pi-hole Secret unless PIHOLE_PASSWORD or PIHOLE_PASSWORD_FILE is supplied here. |
| metrics.image.allowUnverified | bool | `false` |  |
| metrics.image.digest | string | `"sha256:4233c06fcd2d62c37b9345228bc9f798b3a66d052b4955ec6a0e5b8825e20827"` |  |
| metrics.image.pullPolicy | string | `"IfNotPresent"` |  |
| metrics.image.tag | string | `"v1.3.1"` |  |
| metrics.nodeSelector | object | `{}` |  |
| metrics.podAnnotations | object | `{}` |  |
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
| metrics.tolerations | list | `[]` |  |
| minReadySeconds | int | `5` |  |
| monitoring.alerts.enabled | bool | `false` |  |
| monitoring.alerts.for | string | `"2m"` |  |
| monitoring.labels | object | `{}` |  |
| monitoring.namespace | string | `""` | Namespace for ServiceMonitor and PrometheusRule; empty uses the release namespace. |
| monitoring.syntheticDns.enabled | bool | `false` | Run recurring UDP/TCP resolution and optional filtering checks against Service and every replica. |
| monitoring.syntheticDns.schedule | string | `"* * * * *"` |  |
| monitoring.syntheticDns.staleAfterSeconds | int | `180` |  |
| monitoring.syntheticDns.timeoutSeconds | int | `45` |  |
| networkPolicy.allowTests | bool | `true` | Permit release-labelled Helm test and synthetic DNS pods. Disable to supply your own validation policy. |
| networkPolicy.dnsAllowFrom | list | `[]` |  |
| networkPolicy.egress | list | `[]` | DNS-pod egress rules. Include bootstrap DNS, intended upstreams and approved HTTPS mirror destinations. |
| networkPolicy.egressEnabled | bool | `false` | Enable explicit DNS/exporter egress policies. Requires enabled=true; empty rules deny egress. |
| networkPolicy.enabled | bool | `false` |  |
| networkPolicy.metricsAllowFrom | list | `[]` |  |
| networkPolicy.metricsEgress | list | `[]` | Exporter egress rules. Include bootstrap DNS and HTTP access to this release's DNS pods. |
| networkPolicy.webAllowFrom | list | `[]` |  |
| networking.dnsConfig | object | `{}` |  |
| networking.dnsPolicy | string | `""` | Explicit bootstrap DNS policy, or empty for ClusterFirst/ClusterFirstWithHostNet. |
| networking.hostAliases | list | `[]` |  |
| networking.hostNetwork | bool | `false` |  |
| nodeSelector | object | `{}` |  |
| persistence.etcDnsmasq.accessMode | string | `"ReadWriteOnce"` |  |
| persistence.etcDnsmasq.enabled | bool | `false` |  |
| persistence.etcDnsmasq.size | string | `"100Mi"` |  |
| persistence.etcPihole.accessMode | string | `"ReadWriteOnce"` |  |
| persistence.etcPihole.enabled | bool | `true` |  |
| persistence.etcPihole.size | string | `"1Gi"` | Initial per-replica claim size; plan expansion and history retention separately. |
| persistence.retentionPolicy.whenDeleted | string | `"Retain"` |  |
| persistence.retentionPolicy.whenScaled | string | `"Retain"` |  |
| pihole.additionalPackages | string | "" | Additional Alpine packages to install |
| pihole.dnsmasqUser | string | "pihole" | User that FTLDNS runs as |
| pihole.ftl.database.forceDisk | string | `nil` | Use a disk-backed short-term database (FTL v6.8). |
| pihole.ftl.dhcp.ipv6 | bool | `false` | Native DHCPv6 switch. Auxiliary services are outside the DNS HA profile. |
| pihole.ftl.dns.cache.rrtype | string | `nil` | Cached RR types supported by FTL v6.8 (for example ANY). |
| pihole.ftl.files.tmp_db | string | `nil` | Location of the disk-backed temporary database. |
| pihole.ftl.misc.hide_connection_error | string | `nil` | Suppress connection error logging (FTL v6.8). |
| pihole.ftl.resolver.macNames | string | `nil` | Resolve client MAC addresses through configured names. |
| pihole.ftl.resolver.networkNames | boolean | true | Control whether FTL should use the fallback option to try to obtain client names from checking the network table. |
| pihole.ftl.resolver.refreshNames | string | "IPV4_ONLY" | Options: https://docs.pi-hole.net/ftldns/configfile/#refreshnames |
| pihole.ftl.resolver.resolveIPv4 | boolean | true | Whether to resolve IPv4 addresses to hostnames in the query log and other outputs. |
| pihole.ftl.resolver.resolveIPv6 | boolean | true | Whether to resolve IPv6 addresses to hostnames in the query log and other outputs. |
| pihole.ftl.webserver.headers | array | `nil` | Headers to be set on all webserver responses. Check default value in pihole-FTL.toml for format. |
| pihole.ftlCmd | string | "no-daemon" | Customize dnsmasq startup options |
| pihole.gid | integer | 1000 | No description provided. |
| pihole.groups | array | `[]` | Owned groups. Built-in Default is protected. Entries accept name, description and enabled. |
| pihole.lists.adlists | list | `[]` | Subscriptions: address, optional sha256, type (block/allow), enabled, comment, groups. Pinned content is cached on each PVC. |
| pihole.lists.blacklist | list | `[]` |  |
| pihole.lists.regex_blacklist | list | `[]` |  |
| pihole.lists.regex_whitelist | list | `[]` |  |
| pihole.lists.whitelist | list | `[]` |  |
| pihole.tailFtlLog | integer | 1 | Whether to output FTL log (0 or 1) |
| pihole.uid | integer | 1000 | No description provided. |
| pihole.verbose | integer | 0 | Enable verbose startup scripts (0 or 1) |
| podAnnotations | object | `{}` |  |
| podDisruptionBudget.enabled | bool | `false` |  |
| podDisruptionBudget.minAvailable | int | `1` |  |
| podManagementPolicy | string | `"Parallel"` | Stable StatefulSet identity for all replica counts. |
| podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| policy.adoptExisting | bool | `false` | Explicitly adopt matching existing records. Enable only for a reviewed migration, then disable. |
| policy.existingConfigMap | string | `""` | Optional externally retained immutable artifact containing policy.json and the runner files. |
| policy.mode | string | `"reconcile"` | Reconcile owned records, apply without deletion, or explicitly freeze policy management. |
| policy.owner | string | `""` | Explicit original namespace/release identity when restoring a journal into a new release. Empty derives the current identity. |
| policy.requirePinnedLists | bool | `false` | Require SHA-256 pinned HTTPS subscriptions and reject enabled unmanaged feeds. |
| policy.revision | string | `""` | Required SHA-256 of policy.json when referencing an external artifact. |
| policyImportTimeout | int | `180` | Total deadline in seconds for policy validation, reconciliation and gravity. |
| priorityClassName | string | `""` | PriorityClass for the DNS pods. Raises their scheduling priority so a cluster-critical resolver is placed ahead of, and preempts, lower-priority workloads under node pressure. Empty leaves the namespace or cluster default in place. |
| probes.dns.expectedAddress | string | `""` |  |
| probes.dns.name | string | `"pi.hole"` |  |
| probes.liveness.failureThreshold | int | `3` |  |
| probes.liveness.periodSeconds | int | `15` |  |
| probes.liveness.timeoutSeconds | int | `3` |  |
| probes.readiness.failureThreshold | int | `2` |  |
| probes.readiness.periodSeconds | int | `5` |  |
| probes.readiness.timeoutSeconds | int | `3` |  |
| probes.startup.failureThreshold | int | `60` |  |
| probes.startup.periodSeconds | int | `5` |  |
| probes.startup.timeoutSeconds | int | `3` |  |
| replicas | int | `1` |  |
| resources.limits.cpu | string | `"1"` |  |
| resources.limits.memory | string | `"512Mi"` |  |
| resources.requests.cpu | string | `"100m"` |  |
| resources.requests.memory | string | `"128Mi"` |  |
| securityContext.allowPrivilegeEscalation | bool | `false` |  |
| securityContext.capabilities.add | list | `["CHOWN","DAC_OVERRIDE","FOWNER","SETGID","SETUID","SETPCAP","SETFCAP","KILL"]` | Stock-image startup needs ownership, identity/capability setup and child-process shutdown. DHCP adds its network capabilities separately. |
| securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| securityContext.readOnlyRootFilesystem | bool | `false` |  |
| serviceAccount.annotations | object | `{}` |  |
| serviceAccount.automountServiceAccountToken | bool | `false` |  |
| serviceAccount.create | bool | `true` |  |
| serviceAccount.name | string | `""` |  |
| services | object | `{"dhcp":{"annotations":{},"enabled":false,"externalTrafficPolicy":"Local","ports":{"dhcp":67},"type":"LoadBalancer"},"dhcpv6":{"annotations":{},"enabled":false,"externalTrafficPolicy":"Local","ports":{"dhcpv6":547},"type":"LoadBalancer"},"dns":{"annotations":{},"enabled":true,"externalTrafficPolicy":"Local","loadBalancerClass":"","loadBalancerSourceRanges":[],"nodePorts":{},"ports":{"tcp":53,"udp":53},"splitTcpUdp":false,"tcp":{"annotations":{},"enabled":true,"externalTrafficPolicy":"Local","loadBalancerClass":"","loadBalancerSourceRanges":[],"port":53,"type":"ClusterIP"},"type":"ClusterIP","udp":{"annotations":{},"enabled":true,"externalTrafficPolicy":"Local","loadBalancerClass":"","loadBalancerSourceRanges":[],"port":53,"type":"ClusterIP"}},"ntp":{"annotations":{},"enabled":false,"externalTrafficPolicy":"Local","ports":{"ntp":123},"type":"LoadBalancer"},"web":{"annotations":{},"enabled":true,"externalTrafficPolicy":"Local","loadBalancerClass":"","loadBalancerSourceRanges":[],"nodePorts":{},"ports":{"http":80,"https":443},"type":"ClusterIP"}}` | Kubernetes Services are independent of the native FTL configuration tree. |
| services.dns.loadBalancerClass | string | `""` | Optional provider class; immutable after the LoadBalancer Service is created. |
| terminationGracePeriodSeconds | int | `30` |  |
| tests.authentication | bool | `true` | Verify credentials against every replica and the private web Service when enabled. |
| tests.enabled | bool | `true` |  |
| tests.existingSecret | string | `""` | Optional credential Secret for tests when application credentials use an injector. |
| tests.timeoutSeconds | int | `120` |  |
| timezone | string | `"UTC"` |  |
| tolerations | list | `[]` |  |
| topologySpreadConstraints | list | `[]` |  |
| updateStrategy.type | string | `"RollingUpdate"` |  |
| validation.blockedAnswer | string | `"0.0.0.0"` | Expected blocked A answer, or NXDOMAIN when using that blocking mode. |
| validation.blockedDomain | string | `""` | Optional known blocked domain for Helm tests and scheduled DNS checks. No rules are installed implicitly. |
| validation.upstreamDomain | string | `""` | Optional upstream lookup. Empty keeps checks independent of public DNS. |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
