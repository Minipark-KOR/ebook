"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

const ADMIN_PASSWORD = "01074604416";

export default function AdminPage() {
  const [authenticated, setAuthenticated] = useState(false);
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
  } | null>(null);
  // Check sessionStorage on mount
  useEffect(() => {
    if (sessionStorage.getItem("admin_auth") === "1") {
      setAuthenticated(true);
    }
  }, []);

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
      setAuthenticated(true);
      setLoginError(false);
      sessionStorage.setItem("admin_auth", "1");
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
            onClick={() => { setAuthenticated(false); sessionStorage.removeItem("admin_auth"); }}
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
              placeholder="https://bookto31.com/bbs/board.php?bo_table=novel&wr_id=25575"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              지원: bookto31.com / newtoki31.com / toki31.com
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

        {/* Pipeline Progress */}
        {pipelineStatus && (pipelineStatus.current_job || pipelineStatus.loop_running || pipelineStatus.queue.total > 0) && (
          (() => {
            const progress = pipelineStatus.progress;
            const cur = progress?.current;
            const total = progress?.total || 0;
            const index = progress?.index || 0;
            const processed = progress?.processed ?? 0;
            const remaining = progress?.remaining ?? pipelineStatus.queue.total;
            const nextItem = pipelineStatus.queue.next_item;
            // 진행률: collect 단계에서는 current index/total, 아니면 큐 기반 추정
            const pct = total > 0
              ? Math.min(100, Math.round((index / total) * 100))
              : pipelineStatus.queue.total > 0
                ? 100
                : 0;
            const collectTarget = cur || nextItem;

            return (
          <div className="mt-8 bg-white dark:bg-gray-800 rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              진행 중인 작업
            </h2>
            <div className="space-y-4">
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600 dark:text-gray-400">파이프라인 루프</span>
                <span className={`font-medium px-2 py-0.5 rounded ${pipelineStatus.loop_running ? "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200" : "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400"}`}>
                  {pipelineStatus.loop_running ? "실행 중" : "중지됨"}
                </span>
              </div>

              {/* bookto31 수집 현황 */}
              {collectTarget && (
                <div className="text-sm">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-gray-600 dark:text-gray-400">
                      {collectTarget.source || "bookto31"} 수집
                    </span>
                    <span className="font-medium text-gray-900 dark:text-white">
                      {cur ? "수집 중" : "다음 대상"}
                    </span>
                  </div>

                  <p className="text-gray-900 dark:text-white font-medium mb-2">
                    {collectTarget.novel_title || "제목 확인 중"}
                    {collectTarget.chapter ? ` · ${collectTarget.chapter}화` : ""}
                    {collectTarget.wr_id ? ` (wr_id ${collectTarget.wr_id})` : ""}
                    {cur?.attempt && cur.attempt > 1 ? ` · 시도 ${cur.attempt}/3` : ""}
                  </p>

                  {progress && (
                    <>
                      <div className="w-full h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-blue-600 rounded-full transition-all duration-500"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        {total > 0
                          ? `${index}/${total} · 완료 ${processed} · 남음 ${remaining}`
                          : `완료 ${processed} · 남음 ${remaining}`}
                      </p>
                    </>
                  )}
                </div>
              )}

              {/* 현재 처리 중인 회차 */}
              {progress && !collectTarget && (
                <div className="text-sm">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-gray-600 dark:text-gray-400">
                      {progress.phase === "collect" ? "수집 진행도" : progress.phase === "loop" ? "루프 대기" : "현재 작업"}
                    </span>
                    <span className="font-medium text-gray-900 dark:text-white">
                      {progress.phase === "collect" ? "준비 중" : "완료"}
                    </span>
                  </div>
                  <div className="w-full h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-600 rounded-full transition-all duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {total > 0
                      ? `${index}/${total} · 완료 ${processed} · 남음 ${remaining}`
                      : `완료 ${processed} · 남음 ${remaining}`}
                  </p>
                </div>
              )}

              <div>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-gray-600 dark:text-gray-400">큐 진행도</span>
                  <span className="font-medium text-gray-900 dark:text-white">
                    {pipelineStatus.queue.total > 0
                      ? `${pipelineStatus.queue.total}개 대기 중`
                      : "대기열 비어있음"}
                  </span>
                </div>
                <div className="w-full h-3 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-600 transition-all duration-300"
                    style={{
                      width: pipelineStatus.queue.total > 0 ? "100%" : "0%",
                    }}
                  />
                </div>
              </div>

              {pipelineStatus.queue.by_source && Object.keys(pipelineStatus.queue.by_source).length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  <div className="font-medium text-gray-900 dark:text-white mb-1">소스별 대기열</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(pipelineStatus.queue.by_source).map(([source, count]) => (
                      <span
                        key={source}
                        className="px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs"
                      >
                        {source}: {count}개
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {pipelineStatus.queue.by_novel && Object.keys(pipelineStatus.queue.by_novel).length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  <div className="font-medium text-gray-900 dark:text-white mb-1">작품별 대기열</div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(pipelineStatus.queue.by_novel).map(([title, count]) => (
                      <span
                        key={title}
                        className="px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs"
                      >
                        {title}: {count}개
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <p className="mt-4 text-xs text-gray-500 dark:text-gray-400">
              3초마다 자동 새로고침 · 진행 중인 작업이 없으면 숨겨집니다.
            </p>
          </div>
            );
          })()
        )}
      </div>
    </div>
  );
}
