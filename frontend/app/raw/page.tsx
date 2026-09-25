import type { Metadata } from "next";
import { RawEvents } from "./RawEvents";

export const metadata: Metadata = { title: "Greenlight raw events" };

export default function RawPage() {
  return <RawEvents />;
}
