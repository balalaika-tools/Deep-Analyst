import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./styles.css";

export const metadata: Metadata = {
  title: "Deep Analyst",
  description: "Global evidence investigation workspace",
};

export const viewport: Viewport = {
  colorScheme: "dark light",
  themeColor: "#101c1a",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
