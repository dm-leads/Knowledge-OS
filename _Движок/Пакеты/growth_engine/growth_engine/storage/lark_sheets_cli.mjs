/**
 * CLI-мост к Lark Sheets через локальный MCP-сервер (stdio).
 *
 * Зачем отдельная утилита, а не «сделать руками в чате»: таблица юнит-экономики —
 * рабочий инструмент, её листы пересобираются и правятся в каждом цикле гипотез.
 * Разовый скрипт означал бы, что следующая правка делается заново и без гарантий
 * (канон `no-throwaway-scripts-and-no-silent-success`).
 *
 * Работает поверх того же сервера, что и MCP-клиент, с тем же UAT-токеном:
 * авторизация живёт в хранилище сервера, здесь секретов нет.
 *
 * ⚠️ Грабли контура, учтённые здесь:
 *   1. Сервер до правки 11.09.2026 падал на старте из-за PM-контура (POSIX-пути,
 *      uid/gid) — лечится веткой fix/lazy-pm-readers-windows.
 *   2. `10014 app secret invalid` в tenant-режиме — ЛОЖНЫЙ след: приложение
 *      user-режима, ходим только через UAT (память `lark-oauth-false-trails`).
 *   3. Новый лист создаётся с 20 колонками и 200 строками; шире и длиннее — вставкой
 *      (`insert` кладёт строки и колонки ПЕРЕД позицией; за последней строкой вставить нельзя).
 *   4. `values` для записи обязан быть строго прямоугольным — проверяется ниже
 *      до вызова API, иначе запись уходит кривой молча.
 *   5. Не больше 20 диапазонов и 20 000 ячеек за вызов записи (контракт сервера).
 *   6. `lark_sheet_append_rows` отвечает успехом, но ничего не пишет (проба 15.09.2026) —
 *      мост его не использует.
 *   7. Один вызов CLI поднимает свой процесс сервера (~4,3 с) — для хранилища Движка роста
 *      есть постоянный режим `serve`.
 *
 * Команды:
 *   tabs     --book <token>                     — список листов с размерами (row_count, column_count)
 *   read     --book <token> --range "'Лист'!A1:H40"
 *   write    --book <token> --range "'Лист'!A1" --json <файл|-> [--dry-run]
 *   addtab   --book <token> --title "Имя" [--index N]
 *   insert   --book <token> --sheet <sheet_id> --position <номер строки|буква колонки> --count N
 *   serve                                        — постоянный режим: JSON-команда в строке stdin,
 *            {"id", "command", "args"} → {"id", "ok", "result" | "error"} в строке stdout;
 *            команды tabs, read (ranges), write (value_ranges), addtab, insert; у каждой args.book.
 *
 * Примеры:
 *   node lark_sheets_cli.mjs tabs --book QNAosmvHBhiO9gt65r9jiPMzpLf
 *   node lark_sheets_cli.mjs read --book ... --range "'Воронка и оценка гипотез'!A1:L30"
 */
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

// Папка сервера Lark MCP — из LARK_MCP_SERVER_DIR (28.09.2026: мост уезжает в пакет движка и не должен знать
// устройство); без переменной — папка первого инстанса.
const SERVER_DIR = process.env.LARK_MCP_SERVER_DIR
  || String.raw`C:\Users\redmi\Breezeks-Git\06 — Инструменты\MCP\MCP — Lark\server`;
const MAX_RANGES = 20;
const MAX_CELLS = 20_000;

// MCP SDK живёт в node_modules сервера, а не здесь: держать вторую копию
// зависимости ради одной утилиты — лишний источник расхождения версий.
const sdk = (file) =>
  pathToFileURL(path.join(SERVER_DIR, "node_modules", "@modelcontextprotocol", "sdk", "dist", "esm", file)).href;
const { Client } = await import(sdk("client/index.js"));
const { StdioClientTransport } = await import(sdk("client/stdio.js"));

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const options = {};
  for (let i = 0; i < rest.length; i += 1) {
    if (!rest[i].startsWith("--")) continue;
    const key = rest[i].slice(2);
    const next = rest[i + 1];
    if (next === undefined || next.startsWith("--")) {
      options[key] = true;
    } else {
      options[key] = next;
      i += 1;
    }
  }
  return { command, options };
}

function parseToolResult(result) {
  const text = result?.content?.find((item) => item?.type === "text")?.text;
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw_text: text };
    }
  }
  if (result?.isError) {
    const message = payload?.error?.message ?? payload?.errorMessage ?? payload?.message ?? text ?? "MCP tool failed";
    const details = [
      payload?.code !== undefined ? `code=${payload.code}` : null,
      payload?.msg ? `msg=${payload.msg}` : null,
      payload?._hint ? `hint=${payload._hint}` : null
    ].filter(Boolean);
    throw new Error(details.length ? `${message} (${details.join("; ")})` : message);
  }
  return payload ?? {};
}

async function callTool(client, name, data) {
  const result = await client.callTool({
    name,
    arguments: { data: { ...data, profile: "employee" }, useUAT: true }
  });
  return parseToolResult(result);
}

/** Рекурсивно собирает листы: sheet_id → title и размеры, если ответ их содержит. */
function collectTabs(value, output = new Map()) {
  if (!value || typeof value !== "object") return output;
  const properties = value.properties && typeof value.properties === "object" ? value.properties : {};
  const sheetId = value.sheet_id ?? value.sheetId ?? properties.sheet_id ?? properties.sheetId;
  const title = value.title ?? properties.title;
  if (typeof sheetId === "string" && sheetId.trim()) {
    const grid = value.grid_properties ?? properties.grid_properties ?? {};
    output.set(sheetId, {
      sheet_id: sheetId,
      title: typeof title === "string" ? title : sheetId,
      row_count: grid.row_count ?? null,
      column_count: grid.column_count ?? null
    });
  }
  for (const child of Object.values(value)) collectTabs(child, output);
  return output;
}

function readJsonInput(source) {
  const raw = source === "-" ? readFileSync(0, "utf8") : readFileSync(source, "utf8");
  return JSON.parse(raw);
}

/** Прямоугольность матрицы — обязательное требование API, иначе запись молча кривая. */
function assertRectangular(values) {
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error("values: ожидается непустой массив строк");
  }
  const width = Array.isArray(values[0]) ? values[0].length : -1;
  if (width < 0) throw new Error("values: каждая строка должна быть массивом");
  for (const [index, row] of values.entries()) {
    if (!Array.isArray(row) || row.length !== width) {
      throw new Error(`values: строка ${index} длиной ${row?.length}, ожидалось ${width} — матрица не прямоугольная`);
    }
  }
  return { rows: values.length, cols: width };
}

async function resolveToken(client, book) {
  if (!book) throw new Error("book: токен таблицы обязателен");
  const resolved = await callTool(client, "lark_sheet_resolve", { token_or_url: book });
  return resolved?.spreadsheet_token ?? resolved?.token ?? resolved?.data?.spreadsheet_token ?? book;
}

async function listTabs(client, token) {
  const meta = await callTool(client, "lark_sheet_list_tabs", { spreadsheet: token });
  return { spreadsheet_token: token, tabs: [...collectTabs(meta).values()] };
}

async function readRanges(client, token, ranges) {
  if (!Array.isArray(ranges) || ranges.length === 0 || ranges.length > MAX_RANGES) {
    throw new Error(`ranges: от 1 до ${MAX_RANGES} диапазонов`);
  }
  return callTool(client, "lark_sheet_read_ranges", { spreadsheet: token, ranges });
}

async function writeRanges(client, token, valueRanges, dryRun = false) {
  if (!Array.isArray(valueRanges) || valueRanges.length === 0 || valueRanges.length > MAX_RANGES) {
    throw new Error(`value_ranges: от 1 до ${MAX_RANGES} диапазонов`);
  }
  let cells = 0;
  const shapes = valueRanges.map((entry) => {
    if (!entry?.range) throw new Error("value_ranges: у каждого диапазона нужен range");
    const shape = assertRectangular(entry.values);
    cells += shape.rows * shape.cols;
    return { range: entry.range, ...shape };
  });
  if (cells > MAX_CELLS) throw new Error(`value_ranges: ${cells} ячеек, предел ${MAX_CELLS}`);
  const response = await callTool(client, "lark_sheet_write_ranges", {
    spreadsheet: token,
    value_ranges: valueRanges.map(({ range, values }) => ({ range, values })),
    ...(dryRun ? { dry_run: true } : {})
  });
  // Печатаем факт записи, а не «успех» со слов модели; флаг ok сервера ложно ругается на текст с #REF!.
  return { wrote: shapes, cells, response };
}

async function addTab(client, token, title, index) {
  if (!title) throw new Error("title обязателен");
  return callTool(client, "lark_sheet_create_tab", {
    spreadsheet: token,
    title,
    ...(index !== undefined ? { index: Number(index) } : {})
  });
}

async function insertDimension(client, token, sheetId, position, count) {
  if (!sheetId || position === undefined || !(Number(count) > 0)) {
    throw new Error("insert: нужны sheet, position и count больше нуля");
  }
  return callTool(client, "lark_sheet_manage_structure", {
    spreadsheet: token,
    sheet_id: sheetId,
    operation: "insert",
    position: String(position),
    count: Number(count),
    confirm_structural_change: true
  });
}

async function dispatch(client, token, command, args) {
  if (command === "tabs") return listTabs(client, token);
  if (command === "read") return readRanges(client, token, args.ranges);
  if (command === "write") return writeRanges(client, token, args.value_ranges, Boolean(args.dry_run));
  if (command === "addtab") return addTab(client, token, args.title, args.index);
  if (command === "insert") return insertDimension(client, token, args.sheet, args.position, args.count);
  throw new Error(`Неизвестная команда: ${command}`);
}

/** Постоянный режим: одна JSON-команда в строке stdin — один JSON-ответ в строке stdout. */
async function serve(client) {
  const tokens = new Map();
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    let request;
    try {
      request = JSON.parse(line);
    } catch {
      process.stdout.write(`${JSON.stringify({ id: null, ok: false, error: "строка команды — не JSON" })}\n`);
      continue;
    }
    const { id = null, command, args = {} } = request;
    try {
      if (!tokens.has(args.book)) tokens.set(args.book, await resolveToken(client, args.book));
      const result = await dispatch(client, tokens.get(args.book), command, args);
      process.stdout.write(`${JSON.stringify({ id, ok: true, result })}\n`);
    } catch (error) {
      process.stdout.write(`${JSON.stringify({ id, ok: false, error: error.message })}\n`);
    }
  }
}

async function main() {
  const { command, options } = parseArgs(process.argv.slice(2));
  if (!command || options.help) {
    console.log("Команды: tabs | read | write | addtab | insert | serve. См. шапку файла.");
    process.exit(command ? 0 : 1);
  }
  if (command !== "serve" && !options.book) throw new Error("--book <token таблицы> обязателен");

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(SERVER_DIR, "src", "server.mjs")],
    cwd: SERVER_DIR,
    stderr: "pipe"
  });
  const client = new Client({ name: "growth-sheets-cli", version: "1.1.0" });
  await client.connect(transport);

  try {
    if (command === "serve") {
      await serve(client);
      return;
    }
    const token = await resolveToken(client, options.book);
    if (command === "tabs") {
      console.log(JSON.stringify(await listTabs(client, token), null, 2));
      return;
    }
    if (command === "read") {
      if (!options.range) throw new Error("--range обязателен");
      console.log(JSON.stringify(await readRanges(client, token, [options.range]), null, 2));
      return;
    }
    if (command === "write") {
      if (!options.range) throw new Error("--range обязателен");
      if (!options.json) throw new Error("--json <файл|-> обязателен");
      const values = readJsonInput(options.json);
      const written = await writeRanges(client, token, [{ range: options.range, values }], Boolean(options["dry-run"]));
      console.log(JSON.stringify({ wrote: written.wrote[0], range: options.range, response: written.response }, null, 2));
      return;
    }
    if (command === "addtab") {
      console.log(JSON.stringify(await addTab(client, token, options.title, options.index), null, 2));
      return;
    }
    if (command === "insert") {
      console.log(JSON.stringify(
        await insertDimension(client, token, options.sheet, options.position, options.count), null, 2));
      return;
    }
    throw new Error(`Неизвестная команда: ${command}`);
  } finally {
    await client.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error(`ОШИБКА: ${error.message}`);
  process.exit(1);
});
