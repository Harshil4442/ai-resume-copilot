import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { opportunitiesOptions } from "./opportunityQueries";

const get = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ apiGet: get }));
let client: QueryClient;
function Page({ limit }: { limit: number }) {
  const query = useQuery(opportunitiesOptions({ limit }));
  return <p>{query.data ? `${limit}: ${query.data.items.length} rows` : `Loading ${limit}`}</p>;
}
const tree = (limit: number) => <QueryClientProvider client={client}><Page limit={limit} /></QueryClientProvider>;
afterEach(() => { cleanup(); client?.clear(); get.mockReset(); });

it("loads the workspace200 response after a fresh dashboard8 response and reuses only matching limits", async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30000 } } });
  get.mockImplementation(async (url: string) => ({ items: Array(Number(new URL(url, "https://synthetic.test").searchParams.get("limit"))).fill({ id: "synthetic" }), total: 200 }));
  const view = render(tree(8)); await screen.findByText("8: 8 rows");
  view.rerender(tree(200)); await screen.findByText("200: 200 rows");
  expect(get.mock.calls.map(([url]) => url)).toEqual(["/v1/opportunities?limit=8", "/v1/opportunities?limit=200"]);
  view.rerender(tree(8)); await screen.findByText("8: 8 rows"); expect(get).toHaveBeenCalledTimes(2);
});

it("cannot publish a late smaller-limit reply into a navigated workspace query", async () => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30000 } } });
  let finish!: (value: unknown) => void;
  get.mockImplementation((url: string) => url.endsWith("=8") ? new Promise((resolve) => { finish = resolve; })
    : Promise.resolve({ items: Array(200).fill({ id: "synthetic" }), total: 200 }));
  const view = render(tree(8)); await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  const oldSignal = get.mock.calls[0][1] as AbortSignal;
  view.rerender(tree(200)); await screen.findByText("200: 200 rows"); expect(oldSignal.aborted).toBe(true);
  await act(async () => { finish({ items: Array(8).fill({ id: "old" }), total: 200 }); });
  expect(screen.getByText("200: 200 rows")).toBeVisible();
});

it("binds all output-affecting parameters while retaining prefix invalidation", () => {
  const common = { limit: 8 };
  const initial = opportunitiesOptions(common).queryKey;
  for (const changed of [{ limit: 200 }, { stage: "interviewing" }, { search: "engineer" }, { offset: 8 }, { includeArchived: true }]) {
    const key = opportunitiesOptions({ ...common, ...changed }).queryKey;
    expect(key[0]).toBe("opportunities"); expect(key).not.toEqual(initial);
  }
});
