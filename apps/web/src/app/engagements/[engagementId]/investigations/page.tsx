import { InvestigationsPanel } from "@/components/InvestigationsPanel";

export default async function InvestigationsTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <InvestigationsPanel engagementId={engagementId} />;
}
