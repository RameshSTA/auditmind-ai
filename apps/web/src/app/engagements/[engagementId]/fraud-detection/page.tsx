import { FraudDetectionPanel } from "@/components/FraudDetectionPanel";

export default async function FraudDetectionTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <FraudDetectionPanel engagementId={engagementId} />;
}
