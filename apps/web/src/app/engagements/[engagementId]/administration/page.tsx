import { AdministrationPanel } from "@/components/AdministrationPanel";

export default async function AdministrationTab({
  params,
}: {
  params: Promise<{ engagementId: string }>;
}) {
  const { engagementId } = await params;
  return <AdministrationPanel engagementId={engagementId} />;
}
