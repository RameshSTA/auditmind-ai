import { CopilotPanel } from "@/components/CopilotPanel";

export default async function CopilotTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <CopilotPanel engagementId={engagementId} />;
}
