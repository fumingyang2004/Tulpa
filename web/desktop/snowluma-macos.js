'use strict';

/* Compatibility entry for older hosts: the Mac shell uses the unified Docker flow. */
import { mountSnowLuma } from './snowluma.js';

window.TulpaSnowLuma = { mount: mountSnowLuma, mountManaged: mountSnowLuma };
