import { PurchaseDetail } from "@/components/purchase-detail";
export default async function Page({ params }: { params: Promise<{ purchaseId: string }> }) { const { purchaseId } = await params; return <PurchaseDetail purchaseId={purchaseId} />; }
