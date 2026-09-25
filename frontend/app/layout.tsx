import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Sora } from "next/font/google";
import "./globals.css";

// Sora for display, Inter for UI, JetBrains Mono for anything the agent executes.
const sora = Sora({ variable: "--font-sora", subsets: ["latin"] });
const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const jetbrains = JetBrains_Mono({ variable: "--font-jetbrains", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Greenlight",
  description: "An agent that fixes vulnerable dependencies, and merges only with your approval.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sora.variable} ${inter.variable} ${jetbrains.variable} h-full antialiased`}>
      <body className="h-full font-sans text-fg">
        <div aria-hidden className="canvas" />
        {children}
      </body>
    </html>
  );
}
