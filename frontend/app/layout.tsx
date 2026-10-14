import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RepoCompass",
  description: "Ask a code repository a question and get an answer grounded in exact files and lines.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
