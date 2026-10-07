import {nativeIntentPath} from '../lib/native-oauth';

// WebBrowser receives the original callback and verifies the one-use handoff.
// Router navigation to an unknown /oauth route would unmount that login flow.
// A cold-start callback has no in-memory verifier and simply requires sign-in.
export function redirectSystemPath({path}:{path:string;initial:boolean}) {
  return nativeIntentPath(path);
}
