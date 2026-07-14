import { ReportsPanel } from "@/components/ReportsPanel";

export default async function ReportsTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <ReportsPanel engagementId={engagementId} />;
}
