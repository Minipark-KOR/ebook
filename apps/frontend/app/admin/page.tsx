"use client";

import { useState, useEffect, useSyncExternalStore } from "react";
import Link from "next/link";
import { getServerSnapshot, getSnapshot, signIn, signOut, subscribe } from "@/lib/adminAuth";

const ADMIN_PASSWORD = "01074604416";

export default function AdminPage() {
  const authenticated = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const [loginPw, setLoginPw] = useState("");
  const [loginError, setLoginError] = useState(false);

  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{
    ok: boolean;
    source?: string;
    novel_id?: string;
    title?: string;
    message?: string;
    queue_stats?: { total: number; by_source: Record<string, number> };
    loop_running?: boolean;
    detail?: string;
  } | null>(null);

  // Pipeline status polling
  const [pipelineStatus, setPipelineStatus] = useState<{
    loop_running: boolean;
    queue: {
      total: number;
      by_source: Record<string, number>;
      by_novel?: Record<string, number>;
      next_item?: {
        wr_id?: number;
        novel_title?: string;
        chapter?: number | null;
        source?: string;
      } | null;
    };
    current_job?: {
      novel_id: string;
      title: string;
      status: string;
      message?: string;
    } | null;
    jobs?: Array<{
      novel_id: string;
      title: string;
      status: string;
      message?: string;
    }>;
    progress?: {
      phase?: string;
      cycle?: number;
      source?: string;
      current?: {
        wr_id?: number;
        novel_title?: string;
        chapter?: number | null;
        source?: string;
        attempt?: number;
      } | null;
      index?: number;
      total?: number;
      remaining?: number;
      processed?: number;
      last_result?: {
        processed?: number;
        errors?: number;
        remaining?: number;
      };
    };
    novels?: Array<{
      id: string;
      title: string;
      saved: number;
      total: number;
      status?: string;
      queued: boolean;
      collection_done: boolean;
      eta_seconds?: number | null;
    }>;
  } | null>(null);
  // Poll pipeline status whenever authenticated
  useEffect(() => {
    if (!authenticated) return;

    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/pipeline/status");
        if (res.ok) {
          const data = await res.json();
          setPipelineStatus(data);
        }
      } catch (e) {
        console.error("Failed to fetch pipeline status", e);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [authenticated]);

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (loginPw === ADMIN_PASSWORD) {
      setLoginError(false);
      signIn();
    } else {
      setLoginError(true);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url) {
      setResult({ ok: false, message: "작품 URL을 입력하세요" });
      return;
    }
    setLoading(true);
    setResult(null);

    try {
      const res = await fetch("/api/pipeline/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: ADMIN_PASSWORD, url }),
      });
      const data = await res.json();
      if (!res.ok) {
        setResult({ ok: false, message: data.detail || `HTTP ${res.status}` });
      } else {
        setResult(data);
      }
    } catch (err) {
      setResult({ ok: false, message: String(err) });
    } finally {
      setLoading(false);
    }
  }

  // 로그인 화면
  if (!authenticated) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="max-w-sm w-full mx-4">
          <Link href="/" className="text-blue-600 dark:text-blue-400 hover:underline mb-6 inline-block">
            ← 라이브러리로 돌아가기
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">
            관리자 로그인
          </h1>
          <form onSubmit={handleLogin} className="space-y-4 bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <div>
              <label htmlFor="loginPw" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                비밀번호
              </label>
              <input
                id="loginPw"
                type="password"
                value={loginPw}
                onChange={(e) => { setLoginPw(e.target.value); setLoginError(false); }}
                placeholder="관리자 비밀번호를 입력하세요"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                autoFocus
              />
              {loginError && (
                <p className="text-sm text-red-500 mt-1">비밀번호가 일치하지 않습니다</p>
              )}
            </div>
            <button
              type="submit"
              className="w-full px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              로그인
            </button>
          </form>
        </div>
      </div>
    );
  }

  // 관리자 페이지
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 py-8">
      <div className="max-w-2xl mx-auto px-4">
        <div className="flex justify-between items-center mb-6">
          <Link href="/" className="text-blue-600 dark:text-blue-400 hover:underline">
            ← 라이브러리로 돌아가기
          </Link>
          <button
            onClick={() => { signOut(); }}
            className="text-sm text-gray-500 dark:text-gray-400 hover:underline"
          >
            로그아웃
          </button>
        </div>

        <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
          파이프라인 관리
        </h1>
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-8">
          URL을 입력하면 자동으로 사이트를 분기하고 파이프라인을 시작합니다.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4 bg-white dark:bg-gray-800 rounded-lg shadow p-6">
          <div>
            <label htmlFor="url" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              작품 URL
            </label>
            <input
              id="url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://23.ondobook.net/bbs/board.php?bo_table=novel&wr_id=25575"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              지원: 23.ondobook.net / newto31.com / toki31.com / newtoki31.com
            </p>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "파이프라인 시작 중..." : "파이프라인 시작"}
          </button>
        </form>

        {result && (
          <div className={`mt-6 p-4 rounded-lg ${result.ok ? "bg-green-50 dark:bg-green-900/20" : "bg-red-50 dark:bg-red-900/20"}`}>
            {result.ok ? (
              <div className="space-y-2">
                <p className="font-medium text-green-800 dark:text-green-200">
                  ✓ 파이프라인 시작
                </p>
                <div className="text-sm text-gray-700 dark:text-gray-300">
                  <p><b>사이트:</b> {result.source}</p>
                  <p><b>작품 ID:</b> {result.novel_id}</p>
                  <p><b>제목:</b> {result.title}</p>
                  <p><b>상태:</b> {result.message}</p>
                  {result.loop_running && <p><b>루프:</b> 실행 중</p>}
                  {result.queue_stats && (
                    <p>
                      <b>큐:</b> 총 {result.queue_stats.total}개
                      {Object.entries(result.queue_stats.by_source).map(([s, c]) => (
                        <span key={s} className="ml-2">[{s}: {c}개]</span>
                      ))}
                    </p>
                  )}
                </div>
              </div>
            ) : (
              <div>
                <p className="font-medium text-red-800 dark:text-red-200">✗ 오류</p>
                <p className="text-sm text-red-700 dark:text-red-300">{result.message || result.detail}</p>
              </div>
            )}
          </div>
        )}

        {/* 진행 중인 작업 / 완료된 작업 */}
        {pipelineStatus?.novels && pipelineStatus.novels.length > 0 && (() => {
          const nextTitle = pipelineStatus.queue?.next_item?.novel_title;
          const inProgress = pipelineStatus.novels
            .filter((n) => !n.collection_done)
            .sort((a, b) => (a.title === nextTitle ? -1 : 0) - (b.title === nextTitle ? -1 : 0));
          const completed = pipelineStatus.novels.filter((n) => n.collection_done);

          const fmtEta = (sec?: number | null): string | null => {
            if (!sec || sec <= 0) return null;
            const totalMin = Math.ceil(sec / 60);
            const days = Math.floor(totalMin / 1440);
            const hours = Math.floor((totalMin % 1440) / 60);
            const mins = totalMin % 60;
            const d = days > 0 ? `${days}일 ` : "";
            const h = hours > 0 ? `${hours}시간 ` : "";
            const m = mins > 0 ? `${mins}분` : "";
            const approx = days > 0 ? `(약 ${days}일)` : "";
            return `${d}${h}${m}${approx}`.trim();
          };
          const pct = (s: number, t: number) => (t > 0 ? Math.min(100, Math.round((s / t) * 100)) : 0);
          const statusLabel = (s?: string) => {
            if (s === "완결") return "완결";
            if (s === "단편") return "단편";
            return "연재 중";
          };

          return (
            <>
              {inProgress.length > 0 && (
                <div className="mt-8 bg-white dark:bg-gray-800 rounded-lg shadow p-6">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
                    진행 중인 작업
                  </h2>
                  <div className="space-y-5">
                    {inProgress.map((n) => {
                      const p = pct(n.saved, n.total);
                      const eta = fmtEta(n.eta_seconds);
                      return (
                        <div key={n.id}>
                          <p className="font-medium text-gray-900 dark:text-white mb-1">{n.title}</p>
                          <div className="w-full h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden mb-1">
                            <div
                              className="h-full bg-blue-600 transition-all duration-500"
                              style={{ width: `${p}%` }}
                            />
                          </div>
                          <p className="text-sm text-gray-600 dark:text-gray-400">
                            {n.saved}/{n.total} ({p}%)
                            {eta && <span className="ml-2">· 예상 완료까지 {eta}</span>}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {completed.length > 0 && (
                <div className="mt-8 bg-white dark:bg-gray-800 rounded-lg shadow p-6">
                  <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
                    완료된 작업
                  </h2>
                  <div className="space-y-2">
                    {completed.map((n) => (
                      <div key={n.id} className="flex items-center justify-between text-sm">
                        <span className="text-gray-900 dark:text-white font-medium">{n.title}</span>
                        <span className="flex items-center gap-2">
                          <span className="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300">
                            {n.saved}화
                          </span>
                          <span
                            className={`text-xs px-2 py-0.5 rounded text-white ${
                              n.status === "완결" ? "bg-gray-700" : "bg-blue-600"
                            }`}
                          >
                            [{statusLabel(n.status)}]
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          );
        })()}
      </div>
    </div>
  );
}
