# rspamd `override.d`

Drop files here to **fully replace** a stock rspamd config section (as opposed
to `local.d/`, which is merged into the defaults). Most tuning belongs in
`local.d/`; this directory is mounted so it's available when you need a hard
override. It is intentionally (almost) empty.
