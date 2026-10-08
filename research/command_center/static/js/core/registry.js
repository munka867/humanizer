// Workspace registry: the single list of screens. Titles/icons live here so the shell can render navigation before any module loads.
// Owners (who replaces static/js/workspaces/<id>.js): MARKET = command_center, stocks, portfolio; AGENTS = agents; RESEARCH = research, backtest, data, settings.
export const WORKSPACES = [
  { id: "command_center", title: "Command Center", icon: "command_center", owner: "MARKET", key: "1" },
  { id: "stocks", title: "Stocks & Charts", icon: "stocks", owner: "MARKET", key: "2" },
  { id: "portfolio", title: "Portfolio & Orders", icon: "portfolio", owner: "MARKET", key: "3" },
  { id: "agents", title: "Agent Team", icon: "agents", owner: "AGENTS", key: "4" },
  { id: "research", title: "Research & Strategies", icon: "research", owner: "RESEARCH", key: "5" },
  { id: "backtest", title: "Backtesting & Monte Carlo", icon: "backtest", owner: "RESEARCH", key: "6" },
  { id: "data", title: "Data & Connections", icon: "data", owner: "RESEARCH", key: "7" },
  { id: "settings", title: "Settings & Audit", icon: "settings", owner: "RESEARCH", key: "8" },
];
export const DEFAULT_WORKSPACE = "command_center";
export const byId = (id) => WORKSPACES.find(w => w.id === id) || null;
