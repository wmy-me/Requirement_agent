-- 010: 把数据库默认时区设为业务展示时区
--
-- 背景：库内所有时间列都是 `timestamptz`（存的是绝对时刻，**不受本设置影响**），
-- 但「默认时区」决定的是**不经应用、直接用客户端连库**时看到什么。此前数据库默认
-- 是 `Etc/UTC`，于是 DBeaver / psql / Navicat 里看到的时间一律比本地墙钟早 8 小时
-- ——看起来像数据错了，实际只是显示口径不同。
--
-- 应用自身的连接显式带 `-c timezone=UTC`（见 infrastructure/db/session.py），
-- 因此本设置**不影响应用行为**：`NOW()` 仍是同一时刻、naive 值仍按 UTC 解释、
-- 接口返回仍按 display_timezone 转换。它只让直接看库的人看到本地时间。
--
-- 用 current_database() 而非写死库名，避免 POSTGRES_DB 改名后失效。
-- ALTER DATABASE 是幂等的，重复执行只是再次设置同一个值。

DO $$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET timezone TO %L',
        current_database(),
        'Asia/Shanghai'
    );
END $$;
