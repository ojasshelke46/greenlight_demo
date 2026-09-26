import { GreenlightApp } from "@/components/GreenlightApp";
import { ApprovalProvider } from "@/lib/features/approval";

export default function Home() {
  return (
    <ApprovalProvider>
      <GreenlightApp />
    </ApprovalProvider>
  );
}
