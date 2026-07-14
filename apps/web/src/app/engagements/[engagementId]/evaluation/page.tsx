import { EvaluationPanel } from "@/components/EvaluationPanel";

export default async function EvaluationTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <EvaluationPanel engagementId={engagementId} />;
}
