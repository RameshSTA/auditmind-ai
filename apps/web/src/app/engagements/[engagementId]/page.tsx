import { EvidencePanel } from "@/components/EvidencePanel";

export default async function EvidenceTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <EvidencePanel engagementId={engagementId} />;
}
