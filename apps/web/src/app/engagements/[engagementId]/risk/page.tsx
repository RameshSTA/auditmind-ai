import { RiskPanel } from "@/components/RiskPanel";

export default async function RiskTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <RiskPanel engagementId={engagementId} />;
}
