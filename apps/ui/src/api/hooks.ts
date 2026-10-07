import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { del, get, post, put, type QueryValue } from "./client";
import type {
  CaseDetail,
  CaseStep,
  CaseSummary,
  CatalogLogSource,
  CatalogLogSourceUpdate,
  CatalogRule,
  CatalogRuleUpdate,
  CriticalAsset,
  CriticalAssetAdd,
  FeedbackAnswer,
  GroupDetail,
  GroupSummary,
  NotificationRoute,
  OperatorFeedback,
  Page,
  PlatformFlagState,
  QAItemSummary,
  QAResolveRequest,
  RecipientsView,
  Schemas,
  SLAMetrics,
} from "./types";

/** Queues refresh every 15 seconds (api.md "Genel kurallar"). */
export const REFRESH_MS = 15_000;

type Filters = Record<string, QueryValue>;

/** A cursor-paged list that refreshes on its own; `fetchNextPage` is the "next page" button. */
function usePagedList<T>(key: QueryKey, path: string, filters: Filters, refresh = true) {
  return useInfiniteQuery({
    queryKey: [...key, filters],
    queryFn: ({ pageParam }) => get<Page<T>>(path, { ...filters, cursor: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
    refetchInterval: refresh ? REFRESH_MS : false,
  });
}

export const useCases = (filters: Filters) =>
  usePagedList<CaseSummary>(["cases"], "/cases", filters);
export const useQaItems = (filters: Filters) => usePagedList<QAItemSummary>(["qa"], "/qa", filters);
export const useGroups = (filters: Filters) =>
  usePagedList<GroupSummary>(["groups"], "/groups", filters);
export const useCatalogRules = (filters: Filters) =>
  usePagedList<CatalogRule>(["catalog-rules"], "/catalog/rules", filters, false);
export const useCatalogLogSources = (filters: Filters) =>
  usePagedList<CatalogLogSource>(["catalog-log-sources"], "/catalog/log-sources", filters, false);

export const useCase = (caseId: string) =>
  useQuery({
    queryKey: ["case", caseId],
    queryFn: () => get<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`),
    refetchInterval: REFRESH_MS,
  });
export const useCaseSteps = (caseId: string) =>
  useQuery({
    queryKey: ["case-steps", caseId],
    queryFn: () => get<CaseStep[]>(`/cases/${encodeURIComponent(caseId)}/steps`),
  });
export const useCaseFeedback = (caseId: string) =>
  useQuery({
    queryKey: ["case-feedback", caseId],
    queryFn: () => get<FeedbackAnswer[]>(`/cases/${encodeURIComponent(caseId)}/feedback`),
  });
export const useGroup = (groupId: string) =>
  useQuery({
    queryKey: ["group", groupId],
    queryFn: () => get<GroupDetail>(`/groups/${encodeURIComponent(groupId)}`),
    refetchInterval: REFRESH_MS,
  });
export const usePlatformFlags = () =>
  useQuery({
    queryKey: ["platform-flags"],
    queryFn: () => get<PlatformFlagState[]>("/admin/platform-flags"),
  });
export const useCriticalAssets = () =>
  useQuery({
    queryKey: ["critical-assets"],
    queryFn: () => get<CriticalAsset[]>("/critical-assets"),
  });
export const useRecipients = (enabled: boolean) =>
  useQuery({
    queryKey: ["recipients"],
    queryFn: () => get<RecipientsView>("/notification-recipients"),
    enabled,
  });
export const useRoutes = (enabled: boolean) =>
  useQuery({
    queryKey: ["routes"],
    queryFn: () => get<NotificationRoute[]>("/notification-routes"),
    enabled,
  });
export const useSla = (filters: Filters) =>
  useQuery({
    queryKey: ["sla", filters],
    queryFn: () => get<SLAMetrics>("/metrics/sla", filters),
    refetchInterval: REFRESH_MS,
  });

/** A mutation that refreshes the listed query families when it succeeds. */
function useChange<Body, Result>(
  run: (body: Body) => Promise<Result>,
  invalidate: readonly string[],
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async () => {
      await Promise.all(invalidate.map((key) => client.invalidateQueries({ queryKey: [key] })));
    },
  });
}

export const usePostFeedback = (caseId: string) =>
  useChange(
    (body: OperatorFeedback) =>
      post<FeedbackAnswer>(`/cases/${encodeURIComponent(caseId)}/feedback`, body),
    ["case-feedback", "cases", "qa"],
  );
export const useResolveQa = (itemId: string) =>
  useChange(
    (body: QAResolveRequest) =>
      post<Schemas["QAResolveAnswer"]>(`/qa/${encodeURIComponent(itemId)}/resolve`, body),
    ["qa", "case-feedback"],
  );
export const useSaveRule = (ruleId: number) =>
  useChange(
    (body: CatalogRuleUpdate) => put<CatalogRule>(`/catalog/rules/${ruleId}`, body),
    ["catalog-rules"],
  );
export const useAcceptDraft = (ruleId: number) =>
  useChange(() => post<CatalogRule>(`/catalog/rules/${ruleId}/accept-draft`), ["catalog-rules"]);
export const useSaveLogSource = (logSourceId: number) =>
  useChange(
    (body: CatalogLogSourceUpdate) =>
      put<CatalogLogSource>(`/catalog/log-sources/${logSourceId}`, body),
    ["catalog-log-sources"],
  );
export const useStartSync = () =>
  useChange(() => post<Schemas["SyncAccepted"]>("/catalog/sync"), []);
export const useSetFlag = (name: string) =>
  useChange(
    (body: Schemas["PlatformFlagUpdate"]) =>
      put<PlatformFlagState>(`/admin/platform-flags/${encodeURIComponent(name)}`, body),
    ["platform-flags"],
  );
export const useAddAsset = () =>
  useChange(
    (body: CriticalAssetAdd) => post<CriticalAsset>("/critical-assets", body),
    ["critical-assets"],
  );
export const useDeleteAsset = () =>
  useChange(
    (id: string) => del<void>(`/critical-assets/${encodeURIComponent(id)}`),
    ["critical-assets"],
  );
export const useSaveRecipients = (listName: string) =>
  useChange(
    (emails: string[]) =>
      put<Schemas["RecipientGroup"]>(`/notification-recipients/${encodeURIComponent(listName)}`, {
        emails,
      }),
    ["recipients"],
  );
export const useSaveRoutes = () =>
  useChange(
    (routes: Schemas["NotificationRouteEntry"][]) =>
      put<Schemas["NotificationRoute"][]>("/notification-routes", { routes }),
    ["routes"],
  );
