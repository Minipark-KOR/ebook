import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "DevForge",
  description: "DevForge 포털 — 도서관·뉴스·상태",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <nav className="border-b border-gray-200 dark:border-gray-800">
          <div className="mx-auto w-full max-w-5xl p-3 flex gap-4 text-sm">
            <Link href="/" className="font-semibold">DevForge</Link>
            <Link href="/library">도서관</Link>
            <Link href="/news">뉴스</Link>
            <Link href="/status">상태</Link>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
