'use client';

import { Console } from './console';
import { BootGate } from './shell';

export default function Page() {
  return (
    <BootGate>
      <Console />
    </BootGate>
  );
}
