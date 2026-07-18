import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { Activity, BarChart3, Bot, Brain, FileText, Languages, Moon, Sun, Plus, Trash2, Pencil, MessageSquare, ChevronsLeft, ChevronsRight, Settings, Layers, Target, Loader2, GitBranch, Menu, X, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type SessionItem } from "@/lib/api";
import { useAgentStore } from "@/stores/agent";
import { ConnectionBanner } from "@/components/layout/ConnectionBanner";
import { prefetchRoute } from "@/router";

// Bump on each release; one place keeps the footer in sync with package.json.
const APP_VERSION = "v0.1.10";

export function Layout() {
  const { t, i18n: i18nHook } = useTranslation();

  const NAV = [
    { to: "/", icon: BarChart3, label: t('layout.home') },
    { to: "/agent", icon: Bot, label: t('layout.agent') },
    { to: "/runtime", icon: Activity, label: t('layout.runtime') },
    { to: "/reports", icon: FileText, label: t('layout.reports') },
    { to: "/alpha-zoo", icon: Layers, label: t('layout.alphaZoo') },
    { to: "/ml-training", icon: Brain, label: t('layout.mlTraining') },
    { to: "/strategy-zoo", icon: Target, label: t('layout.strategyZoo') },
    { to: "/industry-chain", icon: GitBranch, label: t('layout.industryChain') },
    { to: "/settings", icon: Settings, label: t('layout.settings') },
    { to: "/correlation", icon: BarChart3, label: t('layout.correlation') },
  ];
  const { pathname } = useLocation();
  const [searchParams] = useSearchParams();
  const { dark, toggle } = useDarkMode();
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const sseStatus = useAgentStore(s => s.sseStatus);
  const sseRetryAttempt = useAgentStore(s => s.sseRetryAttempt);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("qa-sidebar") === "collapsed");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mobileViewport, setMobileViewport] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const mobileCloseRef = useRef<HTMLButtonElement>(null);
  const previousPath = useRef(pathname);

  const activeSessionId = searchParams.get("session");
  const streamingSessionId = useAgentStore(s => s.streamingSessionId);

  useEffect(() => {
    localStorage.setItem("qa-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setMobileViewport(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!mobileOpen) return;
    const frame = window.requestAnimationFrame(() => mobileCloseRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [mobileOpen]);

  const loadSessions = () => {
    setSessionsLoading(true);
    setSessionsError(null);
    api.listSessions()
      .then((list) => setSessions(Array.isArray(list) ? list : []))
      .catch((error: unknown) => {
        setSessions([]);
        setSessionsError(error instanceof Error ? error.message : t("layout.sessionsUnavailable"));
      })
      .finally(() => setSessionsLoading(false));
  };

  // Load sessions on mount. Also refresh when navigating TO /agent or when
  // the active session changes (covers new session creation from Agent).
  const isAgentPage = pathname.startsWith("/agent");
  useEffect(() => { loadSessions(); }, [isAgentPage, activeSessionId]);
  useEffect(() => {
    setMobileOpen(false);
    if (previousPath.current === pathname) return;
    previousPath.current = pathname;
    const frame = window.requestAnimationFrame(() => document.getElementById("main-content")?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const deleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      setSessions((prev) => prev.filter((s) => s.session_id !== sid));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("layout.sessionDeleteFailed"));
    }
    setDeleteTarget(null);
  };

  const renameSession = async (sid: string) => {
    if (!renameValue.trim()) { setRenameTarget(null); return; }
    try {
      await api.renameSession(sid, renameValue.trim());
      setSessions((prev) => prev.map((s) => s.session_id === sid ? { ...s, title: renameValue.trim() } : s));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("layout.sessionRenameFailed"));
    }
    setRenameTarget(null);
  };

  return (
    <div className="flex h-dvh min-h-dvh overflow-hidden bg-background">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:rounded-lg focus:bg-primary focus:text-primary-foreground focus:text-sm focus:font-medium"
      >
        Skip to content
      </a>
      {mobileOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          aria-label={t("layout.closeNavigation")}
          onClick={() => setMobileOpen(false)}
        />
      ) : null}
      {/* Sidebar */}
      {(!mobileViewport || mobileOpen) ? (
        <aside
        id="primary-navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-72 shrink-0 flex-col border-r bg-card transition-transform duration-200 md:static md:z-auto md:translate-x-0 md:transition-[width]",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          collapsed ? "md:w-12" : "md:w-64",
        )}
      >
        {/* Brand */}
        <div className={cn("flex min-h-14 items-center border-b", collapsed ? "p-2 md:justify-center" : "p-4")}>
          <Link to="/" onClick={() => setMobileOpen(false)} className={cn("flex min-w-0 flex-1 items-center font-bold text-base tracking-tight", collapsed ? "md:justify-center" : "gap-2")}>
            <BarChart3 className="h-5 w-5 text-primary shrink-0" />
            <span className={cn("truncate", collapsed && "md:hidden")}>Vibe-Trading</span>
          </Link>
          <button
            ref={mobileCloseRef}
            type="button"
            onClick={() => setMobileOpen(false)}
            className="ml-2 inline-flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
            aria-label={t("layout.closeNavigation")}
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        {/* Nav */}
        <nav aria-label={t("layout.primaryNavigation")} className={cn("space-y-0.5", collapsed ? "p-1" : "p-2")}>
          {NAV.map(({ to, icon: Icon, label }) => {
            const text = label;
            return (
              <Link
                key={to}
                to={to}
                onClick={() => setMobileOpen(false)}
                onMouseEnter={() => prefetchRoute[to]?.()}
                onFocus={() => prefetchRoute[to]?.()}
                aria-current={(to === "/" ? pathname === "/" : pathname.startsWith(to)) ? "page" : undefined}
                className={cn(
                  "flex min-h-11 items-center rounded-md text-sm transition-colors",
                  collapsed ? "justify-center p-2" : "gap-3 px-3 py-2",
                  (to === "/" ? pathname === "/" : pathname.startsWith(to))
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
                title={collapsed ? text : undefined}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className={cn(collapsed && "md:hidden")}>{text}</span>
              </Link>
            );
          })}
        </nav>

        {/* Sessions — hidden when collapsed */}
        {!collapsed && (
          <div className="flex-1 overflow-auto border-t mt-2 flex flex-col">
            <div className="flex items-center justify-between px-4 py-2">
              <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <MessageSquare className="h-3.5 w-3.5" />
                Sessions
              </span>
              <Link
                to="/agent"
                onClick={() => setMobileOpen(false)}
                className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label={t('layout.newChat')}
              >
                <Plus className="h-3.5 w-3.5" />
              </Link>
            </div>

            <div className="px-2 pb-2 space-y-0.5 overflow-auto flex-1">
              {sessionsLoading ? (
                <div className="space-y-1.5 px-2 py-1" role="status" aria-label={t("layout.loadingSessions")}>
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-11 rounded-md bg-muted/50 animate-pulse" />
                  ))}
                </div>
              ) : sessionsError ? (
                <div className="mx-2 rounded-md border border-danger/30 bg-danger/5 p-2 text-xs text-danger" role="alert">
                  <p>{t("layout.sessionsUnavailable")}</p>
                  <button type="button" onClick={loadSessions} className="mt-1 inline-flex min-h-11 items-center gap-1 font-medium underline underline-offset-2">
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                    {t("layout.retry")}
                  </button>
                </div>
              ) : sessions.length === 0 ? (
                <p className="px-3 py-2 text-xs text-muted-foreground/60">{t('layout.noSessions')}</p>
              ) : null}
              {sessions.map((s) => {
                const isActive = s.session_id === activeSessionId;
                const isDeleting = deleteTarget === s.session_id;
                const isRenaming = renameTarget === s.session_id;
                return (
                  <div key={s.session_id} className="group relative flex items-center">
                    {isRenaming ? (
                      <input
                        autoFocus
                        value={renameValue}
                        aria-label={t("layout.renameSession")}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") renameSession(s.session_id); if (e.key === "Escape") setRenameTarget(null); }}
                        onBlur={() => renameSession(s.session_id)}
                        className="min-h-11 flex-1 min-w-0 rounded-md border border-primary bg-background px-3 py-2 text-xs outline-none"
                      />
                    ) : (
                      <Link
                        to={`/agent?session=${s.session_id}`}
                        onClick={() => setMobileOpen(false)}
                        className={cn(
                          "flex min-h-11 flex-1 min-w-0 items-center rounded-md border-l-2 pl-3 pr-20 text-xs transition-colors",
                          isActive
                            ? "border-l-primary bg-primary/10 text-primary font-medium"
                            : "border-l-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
                        )}
                        title={s.title || s.session_id}
                      >
                        <span className="flex items-center gap-1.5">
                          {streamingSessionId === s.session_id ? (
                            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
                          ) : (
                            <span className={cn(
                              "h-1.5 w-1.5 rounded-full shrink-0",
                              isActive ? "bg-primary/70" : "bg-muted-foreground/40"
                            )} />
                          )}
                          {s.title || s.session_id.slice(0, 16)}
                        </span>
                      </Link>
                    )}
                    {!isRenaming && isDeleting ? (
                      <div className="absolute right-0.5 flex items-center gap-0.5">
                        <button onClick={() => deleteSession(s.session_id)} className="min-h-11 px-2 text-danger hover:bg-danger/10 rounded text-xs font-medium">{t('layout.confirm')}</button>
                        <button onClick={() => setDeleteTarget(null)} className="min-h-11 px-2 text-muted-foreground hover:bg-muted rounded text-xs">{t('layout.cancel')}</button>
                      </div>
                    ) : !isRenaming ? (
                      <div className="absolute right-1 flex items-center gap-0.5 opacity-100 transition-opacity md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100">
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setRenameTarget(s.session_id); setRenameValue(s.title || ""); }}
                          className="inline-flex h-11 w-9 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                          aria-label={t('layout.rename')}
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setDeleteTarget(s.session_id); }}
                          className="inline-flex h-11 w-9 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-danger"
                          aria-label={t('layout.delete')}
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Spacer when collapsed */}
        {collapsed && <div className="flex-1" />}

        {/* Footer */}
        <div className={cn("border-t", collapsed ? "p-1 flex flex-col items-center gap-1" : "p-3 space-y-2")}>
          {collapsed ? (
            <>
              <button onClick={toggle} className="inline-flex h-11 w-11 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" title={dark ? t('layout.light') : t('layout.dark')}>
                {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
              </button>
              <button onClick={() => setCollapsed(false)} className="inline-flex h-11 w-11 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" title={t('layout.expand')}>
                <ChevronsRight className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <button
                  onClick={toggle}
                  className="flex min-h-11 items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
                  {dark ? "Light" : "Dark"}
                </button>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setCollapsed(true)}
                    className="inline-flex h-11 w-11 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    title={t('layout.collapse')}
                  >
                    <ChevronsLeft className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <button
                  onClick={() => { i18nHook.changeLanguage(i18nHook.language === "zh-CN" ? "en" : "zh-CN"); }}
                  className="flex min-h-11 items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Languages className="h-3.5 w-3.5" />
                  {i18nHook.language === "zh-CN" ? "English" : "中文"}
                </button>
                <p className="text-xs text-muted-foreground/60">{APP_VERSION}</p>
              </div>
            </>
          )}
        </div>
        </aside>
      ) : null}

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex min-h-14 items-center gap-3 border-b bg-card px-3 md:hidden">
          <button
            type="button"
            onClick={() => { setCollapsed(false); setMobileOpen(true); }}
            className="inline-flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={t("layout.openNavigation")}
            aria-controls="primary-navigation"
            aria-expanded={mobileOpen}
          >
            <Menu className="h-5 w-5" aria-hidden="true" />
          </button>
          <Link to="/" className="flex min-w-0 items-center gap-2 font-semibold">
            <BarChart3 className="h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
            <span className="truncate">Vibe-Trading</span>
          </Link>
        </header>
        <ConnectionBanner status={sseStatus} retryAttempt={sseRetryAttempt} />
        <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
