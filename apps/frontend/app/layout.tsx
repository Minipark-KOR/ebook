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
  title: "miniebook",
  description: "전자책 라이브러리",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <nav className="border-b border-gray-200 dark:border-gray-800">
          <div className="mx-auto w-full max-w-6xl p-3 flex gap-4 text-sm">
            <Link href="/" className="font-semibold">miniebook</Link>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
