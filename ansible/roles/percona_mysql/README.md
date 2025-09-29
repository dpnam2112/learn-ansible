# percona_mysql (minimal API)

Installs and manages Percona Server for MySQL 8.x with systemd. Renders a lean `my.cnf` from a small config map.

## Minimal public variables

```yaml
percona_mysql_version: "8.0"
percona_mysql_service_enabled: true
percona_mysql_service_state: started
percona_mysql_root_password: ""

percona_mysql_datadir: /var/lib/mysql
percona_mysql_main_conf: /etc/mysql/my.cnf

percona_mysql_config:
  mysqld:
    bind-address: "127.0.0.1"
    port: 3306
```
