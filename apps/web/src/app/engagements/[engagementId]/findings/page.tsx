import { FindingsPanel } from "@/components/FindingsPanel";

export default async function FindingsTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <FindingsPanel engagementId={engagementId} />;
}
