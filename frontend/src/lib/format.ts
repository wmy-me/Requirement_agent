export const text = (value: unknown, fallback = "未提供"): string => typeof value === "string" && value.trim() ? value : value == null ? fallback : String(value);
export const date = (value: unknown): string => { if (typeof value !== "string" || !value) return "未提供"; const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(parsed); };
export const id = (value: unknown): string => value == null ? "" : String(value);
