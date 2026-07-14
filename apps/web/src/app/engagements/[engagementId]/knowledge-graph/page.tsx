import { KnowledgeGraphPanel } from "@/components/KnowledgeGraphPanel";

export default async function KnowledgeGraphTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <KnowledgeGraphPanel engagementId={engagementId} />;
}
