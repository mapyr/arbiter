/**
 * Layer 2 — thin OpenCode plugin (EXECUTION on OpenCode 1.18.16).
 *
 * Primary path (Podman demo): Hangar → injected arbiter MCP
 *   get_gate_policy → check_coverage → (PLAN_REQUIRED → agent ensure_plan)
 *
 * Fallback (no HANGAR_URL): local ``arbiter check-coverage`` CLI.
 *
 * Modes:
 * - default: gate mutating tools only (edit/write/bash/apply_patch/…).
 * - ARBITER_GATE_ALL=1: gate every tool except an explicit read-only allow-list
 * - ARBITER_ALLOW_BASH=1: do not L2-gate ``bash``
 */
import { spawnSync } from "child_process"
import { mkdirSync, readFileSync, writeFileSync, existsSync, realpathSync } from "fs"
import { join, relative, resolve, sep } from "path"

const MUTATING = new Set(["edit", "write", "bash", "apply_patch", "patch"])
const READ_ONLY = new Set([
  "read",
  "glob",
  "grep",
  "list",
  "ls",
  "search",
  "semanticsearch",
  "codesearch",
  "lsp",
  "todoread",
])

function isHangarMcpTool(tool) {
  // OpenCode names MCP tools like ``hangar_hangar_call`` / ``hangar_hangar_list``.
  // Those must NOT go through L2 check-coverage — Hangar hold + voter quorum
  // already gate mockfs/mockhttp. Gating here fail-closes every MCP call.
  // Agent uses hangar_* to reach arbiter ensure_plan / get_gate_policy too.
  return typeof tool === "string" && tool.startsWith("hangar")
}

function shouldGate(tool) {
  if (isHangarMcpTool(tool)) {
    return false
  }
  if (tool === "bash" && process.env.ARBITER_ALLOW_BASH === "1") {
    return false
  }
  if (process.env.ARBITER_GATE_ALL === "1") {
    return !READ_ONLY.has(tool)
  }
  return MUTATING.has(tool)
}

function realPath(p) {
  try {
    return realpathSync(p)
  } catch {
    return resolve(p)
  }
}

/** Prefer project-relative paths so scope auth/** covers OpenCode abs paths. */
function toWorkspaceRelative(path, directory) {
  if (!path || typeof path !== "string") return path
  if (path.startsWith("bash:")) return path
  const cleaned = path.replace(/^\.\//, "")
  if (!directory) return cleaned
  try {
    const root = realPath(directory)
    const abs = cleaned.startsWith("/") || /^[A-Za-z]:[\\/]/.test(cleaned)
      ? realPath(cleaned)
      : realPath(resolve(root, cleaned))
    const rel = relative(root, abs)
    if (!rel || rel === ".") return cleaned.replace(/\\/g, "/")
    // Outside workspace (or different volume): keep original.
    if (rel.startsWith(`..${sep}`) || rel === "..") return cleaned.replace(/\\/g, "/")
    return rel.split(sep).join("/")
  } catch {
    return cleaned.replace(/\\/g, "/")
  }
}

function extractPaths(tool, args, directory) {
  if (!args || typeof args !== "object") return []
  const out = []
  for (const key of ["filePath", "path", "file", "filepath"]) {
    if (typeof args[key] === "string" && args[key]) out.push(args[key])
  }
  if (Array.isArray(args.files)) {
    for (const f of args.files) {
      if (typeof f === "string") out.push(f)
      else if (f && typeof f.path === "string") out.push(f.path)
    }
  }
  if (tool === "bash" && typeof args.command === "string") {
    out.push(`bash:${args.command.slice(0, 200)}`)
  }
  if (typeof args.patchText === "string") {
    const re = /\*\*\* (?:Update|Add) File: (.+)/g
    let m
    while ((m = re.exec(args.patchText)) !== null) {
      out.push(m[1].trim())
    }
  }
  return out.map((p) => toWorkspaceRelative(p, directory))
}

function hangarConfigured() {
  return Boolean(
    process.env.HANGAR_URL || process.env.HANGAR_MCP_URL,
  ) && Boolean(process.env.HANGAR_API_KEY)
}

function arbiterBin() {
  return process.env.ARBITER_BIN || "arbiter"
}

function runJson(bin, args, { directory, timeoutMs }) {
  const proc = spawnSync(bin, args, {
    cwd: directory || process.cwd(),
    env: process.env,
    encoding: "utf8",
    timeout: timeoutMs,
  })
  if (proc.error || proc.status === null) {
    return {
      ok: false,
      error: `arbiter_unavailable:${proc.error?.message || "spawn_failed"}`,
      parsed: null,
      status: proc.status,
    }
  }
  let parsed = null
  try {
    parsed = JSON.parse((proc.stdout || "").trim() || "{}")
  } catch {
    return {
      ok: false,
      error: `arbiter_parse_error:status=${proc.status}`,
      parsed: null,
      status: proc.status,
    }
  }
  return { ok: true, error: null, parsed, status: proc.status }
}

function hangarTool(tool, argumentsObj, { directory }) {
  const server =
    process.env.ARBITER_MCP_SERVER ||
    process.env.ARBITER_HANGAR_MCP_SERVER ||
    "arbiter"
  const timeoutMs = Number(process.env.ARBITER_COVERAGE_TIMEOUT_MS || 120000)
  const res = runJson(
    arbiterBin(),
    [
      "hangar-call",
      "--mcp-server",
      server,
      "--tool",
      tool,
      "--arguments-json",
      JSON.stringify(argumentsObj || {}),
    ],
    { directory, timeoutMs },
  )
  if (!res.ok) {
    return { error: res.error, data: null }
  }
  if (res.parsed && res.parsed.ok === false && res.parsed.error) {
    return { error: String(res.parsed.error), data: null }
  }
  return { error: null, data: res.parsed }
}

function askCoverageLocal({ paths, tool, directory }) {
  const env = process.env
  const breakGlass = env.ARBITER_BREAK_GLASS === "1"
  const args = ["check-coverage", "--json", "--tool", tool]
  for (const p of paths) {
    args.push("--path", p)
  }
  if (breakGlass) {
    args.push("--break-glass")
    if (env.ARBITER_BREAK_GLASS_REASON) {
      args.push("--break-glass-reason", env.ARBITER_BREAK_GLASS_REASON)
    }
  }
  if (env.USER) args.push("--actor", env.USER)
  const timeoutMs = Number(env.ARBITER_COVERAGE_TIMEOUT_MS || 15000)
  const res = runJson(arbiterBin(), args, { directory, timeoutMs })
  if (!res.ok || !res.parsed || typeof res.parsed.approved !== "boolean") {
    return {
      approved: false,
      reason: res.error || "arbiter_invalid_response",
    }
  }
  return res.parsed
}

function askCoverageHangar({ paths, tool, directory }) {
  const breakGlass = process.env.ARBITER_BREAK_GLASS === "1"
  const args = {
    paths,
    tool,
    actor: process.env.USER || undefined,
    break_glass: breakGlass,
    break_glass_reason: process.env.ARBITER_BREAK_GLASS_REASON || undefined,
  }
  const { error, data } = hangarTool("check_coverage", args, { directory })
  if (error || !data || typeof data.approved !== "boolean") {
    return {
      approved: false,
      reason: error || "arbiter_invalid_response",
    }
  }
  return data
}

function getGatePolicy({ directory }) {
  if (hangarConfigured()) {
    const { error, data } = hangarTool("get_gate_policy", {}, { directory })
    if (error || !data?.plan) {
      return {
        plan: {
          mode: "on_uncovered",
          arbiter_mcp_server: process.env.ARBITER_MCP_SERVER || "arbiter",
        },
        _error: error,
      }
    }
    return data
  }
  const timeoutMs = Number(process.env.ARBITER_COVERAGE_TIMEOUT_MS || 15000)
  const res = runJson(arbiterBin(), ["get-gate-policy", "--json"], {
    directory,
    timeoutMs,
  })
  if (!res.ok || !res.parsed?.plan) {
    return {
      plan: {
        mode: "on_uncovered",
        arbiter_mcp_server: "arbiter",
      },
    }
  }
  return res.parsed
}

function sessionPlanPath(directory) {
  return join(directory || process.cwd(), ".arbiter", "session-plan.json")
}

function readSessionPlan(directory) {
  const p = sessionPlanPath(directory)
  if (!existsSync(p)) return null
  try {
    return JSON.parse(readFileSync(p, "utf8"))
  } catch {
    return null
  }
}

function writeSessionPlan(directory, payload) {
  const dir = join(directory || process.cwd(), ".arbiter")
  mkdirSync(dir, { recursive: true })
  writeFileSync(sessionPlanPath(directory), JSON.stringify(payload, null, 2) + "\n")
}

function planRequiredMessage({ mode, server, coverage }) {
  const uncovered =
    Array.isArray(coverage?.uncovered) && coverage.uncovered.length
      ? ` uncovered=${coverage.uncovered.join(",")}`
      : ""
  const decision = coverage?.decision_id ? ` decision=${coverage.decision_id}` : ""
  return (
    `ARBITER_PLAN_REQUIRED: mode=${mode} mcp_server=${server}` +
    ` reason=${coverage?.reason || "no_covering_allow_decision"}${decision}${uncovered}. ` +
    `Call Hangar hangar_call → ${server}/ensure_plan with structured plan ` +
    `{goal, steps:[{action, paths?}], scope:[...]} (Arbiter formulation applies; ` +
    `no universal scope). Then retry the tool.`
  )
}

export const ArbiterGate = async ({ directory }) => {
  const root = directory || process.cwd()
  return {
    "tool.execute.before": async (input, output) => {
      const tool = input.tool
      if (!shouldGate(tool)) return

      const paths = extractPaths(tool, output.args, root)
      const pathList = paths.length ? paths : ["(unspecified)"]
      const policy = getGatePolicy({ directory: root })
      const mode = policy?.plan?.mode || "on_uncovered"
      const server =
        policy?.plan?.arbiter_mcp_server ||
        process.env.ARBITER_MCP_SERVER ||
        "arbiter"
      // Prefer policy server name for subsequent hangar calls in this turn.
      if (server) process.env.ARBITER_MCP_SERVER = server

      const session = mode === "session" ? readSessionPlan(root) : null
      const coverage = hangarConfigured()
        ? askCoverageHangar({ paths: pathList, tool, directory: root })
        : askCoverageLocal({ paths: pathList, tool, directory: root })

      if (coverage.approved) {
        if (mode === "session" && coverage.decision_id) {
          writeSessionPlan(root, {
            approved: true,
            decision_id: coverage.decision_id,
            at: new Date().toISOString(),
          })
        }
        return
      }

      // session: first mutation (or uncovered paths) needs ensure_plan.
      // on_uncovered: only when check_coverage fail-closes.
      if (mode === "session" && !session?.approved) {
        throw new Error(
          planRequiredMessage({
            mode,
            server,
            coverage: { ...coverage, reason: coverage.reason || "session_plan_missing" },
          }),
        )
      }
      throw new Error(planRequiredMessage({ mode, server, coverage }))
    },
  }
}
