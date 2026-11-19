import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

const ibmPlexSans = IBM_Plex_Sans({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "VXG Tracker — Bảng giá Vàng, Xăng dầu & Gas tại Việt Nam",
  description:
    "Hệ thống tự động thu thập, kiểm chứng nguồn gốc và hiển thị bảng giá hàng hóa thiết yếu tại Hà Nội, Đà Nẵng và TP. Hồ Chí Minh.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi">
      <body
        className={`${ibmPlexSans.className} ${ibmPlexMono.variable} min-h-screen bg-[#F8FAFC] text-[#0F172A] antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
