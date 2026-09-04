import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Autonomous Incident Response",
  description: "Enterprise incident response dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif" }}>
        <header style={{ padding: "16px 24px", borderBottom: "1px solid #eee", background: "#f8f9fa" }}>
          <h1 style={{ margin: 0, fontSize: "20px" }}>
            🚨 Autonomous Incident Response
          </h1>
        </header>
        <main style={{ padding: "24px" }}>{children}</main>
      </body>
    </html>
  );
}
