-- MariaDB bootstrap da MegaPromoOz (rodado UMA vez por ops com conta privilegiada).
-- Preferir infra/scripts/provision-db.sh, que gera as senhas no host e nunca as imprime.
--
-- Um usuário por função, menor privilégio:
--   megapromooz_migrate   DDL + DML no schema `megapromooz` (Alembic)
--   megapromooz_app       só DML (runtime: web e worker)

CREATE DATABASE IF NOT EXISTS `megapromooz` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'megapromooz_migrate'@'%' IDENTIFIED BY '<MIGRATE_PASSWORD>';
CREATE USER IF NOT EXISTS 'megapromooz_app'@'%' IDENTIFIED BY '<APP_PASSWORD>';

GRANT ALL PRIVILEGES ON `megapromooz`.* TO 'megapromooz_migrate'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `megapromooz`.* TO 'megapromooz_app'@'%';

FLUSH PRIVILEGES;

-- Rede: a 3306 está fechada na interface pública (nft `inet mb_guard`); containers usam
-- host.docker.internal / 172.17.0.1 e admins, túnel SSH.
