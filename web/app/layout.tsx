import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "AJORA · Academic opportunities across India", template: "%s · AJORA" },
  description: "Find academic workshops, conferences and competitions from institutions across India.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
