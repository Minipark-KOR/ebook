"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

const ADMIN_PASSWORD = "01074604416";

export default function AdminPage() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loginPw, setLoginPw] = useState("");
  const [loginError, setLoginError] = useState(false);

  const [password, setPassword] = useState("");
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

  // Check sessionStorage on mount
  useEffect(() => {
    if (sessionStorage.getItem("admin_auth") === "1") {
      setAuthenticated(true);
    }
  }, []);

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
    if (!password || !url) {
      setResult({ ok: false, message: "비밀번호와 URL을 모두 입력하세요" });
      return;
    }
    setLoading(true);
    setResult(null);

    try {
      const res = await fetch("/api/pipeline/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password, url }),
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
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              비밀번호
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="관리자 비밀번호"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
          </div>

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
      </div>
    </div>
  );
}
