export const text = (value: unknown, fallback = "未提供"): string => typeof value === "string" && value.trim() ? value : value == null ? fallback : String(value);
export const date = (value: unknown): string => { if (typeof value !== "string" || !value) return "未提供"; const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(parsed); };
export const id = (value: unknown): string => value == null ? "" : String(value);
/** Short, single-line excerpt for evidence lists. Never invents text. */
export const excerpt = (value: unknown, max = 90): string => { const raw = text(value, "").replace(/\s+/g, " ").trim(); if (!raw) return ""; return raw.length > max ? `${raw.slice(0, max)}…` : raw; };
/** Render a list of real values, or an explicit 未提供 when empty. */
export const joined = (values: string[] | null | undefined, separator = "、"): string => { const list = (values ?? []).filter((v) => typeof v === "string" && v.trim()); return list.length ? list.join(separator) : "未提供"; };
