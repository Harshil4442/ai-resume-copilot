import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("../../components/BrowserAssistanceReview",()=>({default:()=> <div>Application assistance review</div>}));
vi.mock("../../components/BrowserPairingReview",()=>({default:({pairingId}:{pairingId:string})=> <div>Native connection {pairingId}</div>}));
vi.mock("../../components/BrowserDeviceRevocation",()=>({default:({deviceId}:{deviceId:string})=> <div>Native disconnection {deviceId}</div>}));
import Page from "./page";
const id="00000000-0000-4000-8000-000000000001";
afterEach(cleanup);
it("routes the native candidate approval URL to its actual password review",async()=>{
  render(await Page({searchParams:Promise.resolve({pairing:id})}));expect(screen.getByText(`Native connection ${id}`)).toBeVisible();
  expect(screen.queryByText("Application assistance review")).toBeNull();
});
it("routes completed native device disconnection separately",async()=>{
  render(await Page({searchParams:Promise.resolve({native_device:id})}));expect(screen.getByText(`Native disconnection ${id}`)).toBeVisible();
});
it("rejects conflicting native and application identities",async()=>{
  render(await Page({searchParams:Promise.resolve({pairing:id,review:id})}));expect(screen.getByRole("alert")).toHaveTextContent("one exact");
});
it("preserves the older assistance query combinations",async()=>{
  render(await Page({searchParams:Promise.resolve({device:id,application:"synthetic-app"})}));expect(screen.getByText("Application assistance review")).toBeVisible();
});
