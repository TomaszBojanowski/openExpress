# Design: adding a device to the Garmin account

Status: implemented; experimental until a second device is added this way, by another user on
another account (4.7).

## 1. Scope

openExpress uses a device with Garmin Connect (sync, Connect IQ apps, the primary training
device) only when the account it is logged in to lists that device; otherwise it refuses, says
why, and changes nothing. Before this change a device the account does not list could be added
only elsewhere: the Garmin Connect app on a phone, or Garmin Express on another computer. This
design adds it to openExpress:

1. **On request only**: a button in the window and a command, never on its own (D1).
2. **After a confirmation dialog** that names the device, the account and what follows (D3).
3. **One request** that adds the device, as Garmin Express makes it, and a second only for a
   device Garmin keeps as retired (D2, D6); then the account's list read anew decides whether it
   worked (D7).
4. **What Garmin refuses**, said plainly: a device on another account, with an active
   subscription of another account, a description Garmin does not accept, a device Garmin does
   not allow (D5).
5. **Marked experimental**, in the window and on the command line (4.7).

Not in the scope: everything else Garmin Express does when it adds a device (section 2), namely
writing the device's own Garmin Connect credentials, the user's profile, e-mail address or phone
number to the device, its nickname, Wi-Fi, time zone and preferred activity tracker (D4, D10);
comparing the account's GUID with the one the device keeps, and deleting files on a device that
was paired with another account (D5); removing a device from an account (only Garmin Connect
does).

An example of the case it solves: a fēnix 5X registered only as a product on garmin.com, not in
Garmin Connect ([#27](https://github.com/TomaszBojanowski/openExpress/issues/27)). The account's
list did not hold it, so openExpress refused it; once it was added in Garmin Connect, a sync
worked. With this feature it is added from openExpress, after the confirmation dialog.

## 2. What Garmin Express does

From the analysis of Garmin Express (Windows 7.29.1.0, macOS 7.29.3.0): facts about its use of
the servers, written down without its code.

After every sign-in, for the device it signs in for, in this order:

1. The ticket of the sign-in page exchanged for the account's token; the account's profile.
2. The privacy consents of Garmin Connect (and golf); none given: the token is dropped.
3. On an MTP device: the account's GUID compared with the one the device keeps (an MTP
   operation of Garmin's). A device paired with another account: the user is asked, and the
   device's files may be deleted.
4. **The registration**: `POST device-service/deviceregistration/device` on the Garmin Connect
   API host, the device's whole `GarminDevice.xml` as the body (`Content-Type:
   application/xml`), with the header `Override: 1` ("add as active") for devices that track
   activity (`Override: 2` means "add as inactive").
   - 409: the device belongs to another account; Garmin Express may offer a factory reset and
     try again, three times at most. 409 with `error.subcode` 4091: another account has an
     active subscription on it; Garmin Express stops.
   - 400: the description is not accepted (bad XML, no UDI); 403: registration not allowed.
   - 2xx: JSON with `unitId`, `displayName`, `partNumber`, `newRegistration`, `deviceStatus`
     and the device's own OAuth credentials (`consumer`, `secret`, `oAuthToken`,
     `oAuthTokenSecret`).
   - `deviceStatus` "retired": `PUT device-service/deviceregistration/device/status/{unitId}`
     with `{"deviceStatus":"active"}`.
5. The account's settings; the user's physiological profile, e-mail address, phone number and
   user name written to the device's settings.
6. The device's OAuth credentials written to the device (an encrypted settings file, or its
   Wi-Fi file), with which the device later uploads by itself over Wi-Fi.
7. For new accounts and Wi-Fi scales, the time zone; then the primary training device and the
   preferred activity tracker (a question "Do you want this to be your Preferred Activity
   Tracker?").
8. Any error in the chain: the device's token is dropped.

On macOS the same steps are a wizard: nickname, registration (e-mail, privacy consent), account
(Sign In / Create an Account), Wi-Fi, the device's settings, the Garmin Connect profile, a first
sync, and "Setup complete! This device is now registered to" the account.

Garmin Express never reads the account's list (`device-service/deviceregistration/devices`):
it learns the account's devices from `express-gateway/device-info/primary-training-device`
(`RegisteredDevices`), and whether a device is the account's from the registration's answer.
openExpress asks the list before every account operation; the registration is the one request
this design adds.

## 3. Before this change

- `service.require_account_device`: the list asked (kept five minutes, asked anew before a write
  or an upload, forgotten at every sign-in and sign-out); a device not on it raises
  `NotAccountDevice`, whose message says to sign in to the account the device belongs to, to add
  it in Garmin Connect, and to check that the phone's Garmin Connect app uses the same account;
  the log tells what the list holds (part numbers, states, how the Unit IDs compare, the
  environment), never a Unit ID.
- The window: the banner "This device is not registered to your Garmin account" (no button),
  Sync off, the Apps page and the primary training device "Not available".
- The command line: account commands end with code 1 and the same message.

## 4. The window and the command line

### 4.1 Where it is offered

Only for a device the account's list does not hold (`NotAccountDevice`, not a list that could
not be read), while logged in, and for a device that syncs (MTP, or a USB drive whose
`GarminDevice.xml` declares what it uploads; not a Varia, which Garmin Express does not register
either):

- the banner gets the button **Add to Account**;
- the Device page gets, in its group **Garmin Connect Account**, the row **Garmin Account** –
  "Not on the account {account}" – with the same button, above **Primary Training Device**. The
  row is shown only for a device off the account: a device added loses it, and the group then
  shows what it shows for any device of the account. Not implemented: the row's "Yes, on the
  account {account}" for a device on the account.

### 4.2 The confirmation dialog and the work

**Add to Account** asks (`confirm()`, Cancel by default, Escape and closing are Cancel):

> **Add fēnix 5X to Your Garmin Account?**
> openExpress sends Garmin the description of the device (its GarminDevice.xml, with its Unit
> ID) and adds it to the account {account} as an active device. Then a sync uploads its
> activities and health data to this account.
>
> If the device is paired with the Garmin Connect app on a phone, check first that the app uses
> the same account. A device can be removed from an account only in Garmin Connect.
>
> This is an experimental feature.
>
> Cancel · **Add to Account**

{account} is the name the window already shows for the account (Preferences, "Signed in as"):
its user name, else its whole e-mail address. The command line masks it: a user name as `t…b`,
an e-mail address as `t…@b…` (D3). The last line says the feature is experimental (4.7).

Then, as a task, "Adding to the Account {account}…" (the row's button spinning when it started
there): the list asked anew (the device may have been added meanwhile: then nothing is sent),
the request, the status changed for a retired device (D6), the list asked anew.

### 4.3 The outcomes

- **Added**: the toast "Added to your Garmin account" (one line, at 360 px too; the account is
  on the page); the device checked again, so the banner and the row **Garmin Account** go, Sync,
  Apps and the primary training device come back. Nothing is synced by itself.
- **Already on the account** (the list read before the request holds it): the toast "On your
  Garmin account already", no request; the device checked again.
- **On another account (409)**: `inform()` "Device on Another Account": "This device is
  registered to another Garmin account. If it is your other account, sign in to it in
  openExpress. If it was someone else's, they have to remove it from their account in Garmin
  Connect; then add it here. Nothing was changed." Not implemented: the row's "On another Garmin
  account" after it.
- **Another account's subscription (409, subcode 4091)**: "Subscription on Another Account":
  "Another Garmin account has an active subscription on this device; its owner has to end it
  first. Nothing was changed."
- **Not accepted (400)**, **not allowed (403)**: "Device Not Added": "Garmin did not accept the
  description of this device. Nothing was changed." / "Garmin does not allow adding this device
  to an account. Nothing was changed." A 403 with the computer's clock off: the clock's usual
  message.
- **Accepted, not on the list yet**: "Device Not Listed Yet": "Garmin accepted the device, but
  the account's list does not show it yet. Refresh in a few minutes. If it is still missing,
  check the devices of your account in Garmin Connect."
- **Accepted, the list not read**: "Garmin Accepted the Device": "The account's list could not
  be read to check it. Refresh in a few minutes; if the device is still missing, check the
  devices of your account in Garmin Connect."
- **An answer that names another device** (D7): the window's usual error, "Garmin accepted the
  request, but its answer names another device. Check the devices of your account in Garmin
  Connect."
- **Network error or no answer**: whether Garmin added it is not known; the list is asked again
  on Refresh, the request is never repeated by itself.

### 4.4 Texts (English, Polish)

| English | Polish |
|---|---|
| Add to Account | Dodaj do konta |
| Garmin Account / Not on the account {account} | Konto Garmin / Nie ma go na koncie {account} |
| Yes, on the account {account} (not implemented) | Tak, na koncie {account} |
| Adding to the Account {account}… | Dodawanie do konta {account}… |
| Add {model} to Your Garmin Account? | Dodać {model} do konta Garmin? |
| (the body of 4.2) | openExpress wyśle do Garmina opis urządzenia (plik GarminDevice.xml z jego numerem Unit ID) i doda je do konta {account} jako aktywne. Potem synchronizacja wyśle na to konto jego aktywności i dane o zdrowiu. … |
| This is an experimental feature. | To funkcja eksperymentalna. |
| Added to your Garmin account / On your Garmin account already | Dodano do konta Garmin / Już na koncie Garmin |
| Device on Another Account / Subscription on Another Account / Device Not Added | Urządzenie na innym koncie / Subskrypcja na innym koncie / Nie dodano urządzenia |
| On another Garmin account (not implemented) | Na innym koncie Garmin |
| Garmin did not accept the description of this device. Nothing was changed. | Garmin nie przyjął opisu tego urządzenia. Niczego nie zmieniono. |
| Garmin does not allow adding this device to an account. Nothing was changed. | Garmin nie pozwala dodać tego urządzenia do konta. Niczego nie zmieniono. |
| Device Not Listed Yet / Garmin Accepted the Device | Urządzenia jeszcze nie ma na liście / Garmin przyjął urządzenie |
| Garmin accepted the device, but the account's list does not show it yet. … | Garmin przyjął urządzenie, ale lista konta jeszcze go nie pokazuje. … |

### 4.5 The command line

`openexpress register` (D9), a dry run without `--apply`, as every command that changes
something:

```
$ openexpress register
fenix 5x (006-B2604-00, software 19.10) is not on the Garmin account t…b.
Would add it as an active device: POST connectapi.garmin.com/device-service/deviceregistration/device
with its GarminDevice.xml (2.1 KB; it holds the device's Unit ID).
After that, a sync uploads its activities and health data to this account.
A device can be removed from an account only in Garmin Connect.
This is an experimental feature.
Dry run: nothing was changed. Add --apply.
$ openexpress register --apply
Added fenix 5x to the Garmin account t…b.
```

- A device on the account already: "fenix 5x is on the Garmin account t…b already." (code 0,
  nothing sent).
- A device Garmin kept as retired: one more line, "Garmin kept it as retired; it is active now."
- Refusals: the texts of 4.3, code 1; so are a device Garmin accepted that the account's list
  does not show yet, and a list that could not be read to check it. No new exit code.
- An account named by an e-mail address shows as `t…@b…`; every command shows the account
  masked so.

### 4.6 How the request introduces itself

As every request of openExpress to the Garmin Connect API: signed (OAuth1) with Garmin Express's
application key and its secret, read at setup from the user's own copy of the official
installer, and the account's token; with the client headers of Garmin Express
(`Garmin-Client-Name`, `-Version`, `-Platform`, `-Platform-Version`, `-SessionId`,
`X-garmin-client-id`), as for every other request; and with openExpress's own User-Agent,
`openexpress/<version> (Linux)`, the one every request of the program sends. The body is the
device's `GarminDevice.xml`, with `Override: 1`, as Garmin Express sends it. Garmin Express's own
client sends there the default User-Agent of its HTTP library, known from the analysis only, not
from its traffic: openExpress does not imitate it. Garmin accepted the first device added this
way (4.7) with openExpress's own User-Agent.

### 4.7 Experimental

Adding a device is the first request of openExpress that Garmin's servers answer with a change
to the account's devices. The tests answer as Garmin's servers would, from the analysis of
Garmin Express; only real requests show that Garmin takes it. A first device has been added this
way: a Venu 2 not on the account, from the window; Garmin's server answered 200 to the
registration, and the device was on the account's list after it, as active. Until a second
attempt succeeds, by a user other than the one of the first, on another account, the feature
says it is experimental: the confirmation dialog ends with "This is an experimental feature.",
the command's help reads "add the connected device to your Garmin account (experimental; a dry
run without --apply)", its dry run says "This is an experimental feature.", and the
documentation calls it experimental.

## 5. Decisions

### D1: when a device is added — chosen: 1a

- **1a. Only when asked (recommended; chosen):** the button of 4.1 or `register --apply`; nothing at
  sign-in or when a device is plugged in.
  - \+ Writing to the account stays a choice made each time, as every other write of openExpress.
  - − One click more than Garmin Express, which registers at every sign-in.
- **1b. At sign-in, as Garmin Express.**
  - − The account changes without asking; a device of someone else, plugged in once, would
    be tried on the user's account (Garmin refuses it only if it is on another account).

### D2: active or inactive — chosen: 2a

- **2a. Active, `Override: 1` (recommended; chosen):** as Garmin Express adds a device that tracks
  activity, and as the phone app leaves a paired device.
  - \+ The state the device is known to sync in.
  - − Whether an active second device changes how Garmin Connect counts steps or which device it
    prefers is Garmin's rule; openExpress changes neither the primary training device nor the
    preferred activity tracker (D10).
- **2b. Inactive, `Override: 2`.**
  - \+ The least change to the account.
  - − What an inactive device may upload is not known; the device could be on the account and
    still not serve its purpose.
- **2c. No header** (the server's default, not known).

### D3: the confirmation dialog — chosen: 3a

- **3a. The device, the account, what is sent and what follows (recommended; chosen):** the text of
  4.2; the account named as the window names it elsewhere (its user name, else its whole e-mail
  address); masked on the command line only (a user name and an e-mail address alike, as in its
  other commands), since its output is pasted into reports.
  - \+ The account named is the check that the device goes where the user means; the advice
    about the phone's app catches the commonest mistake (two accounts) before a request.
- **3b. A short confirmation dialog** ("Add this device to your account?").
  - − Says neither where nor what follows.

### D4: the device's credentials in the answer — chosen: 4a

- **4a. Never kept, logged or written to the device (recommended; chosen):** only `newRegistration` and
  `deviceStatus` are read; the body is not kept, not even in an error or the log.
  - \+ Nothing new to protect; the device keeps uploading as before (through the phone's app,
    its Wi-Fi as set up there, or openExpress).
  - − A device that uploads by itself over Wi-Fi only with credentials written by Garmin Express
    gets none from openExpress.
- **4b. Written to the device as Garmin Express does.**
  - − An encrypted file of the device's whose format openExpress does not have and, by its
    clean-room rule, does not reconstruct.

### D5: a device on another account — chosen: 5a

- **5a. Refused, explained, nothing more (recommended; chosen):** 409 and 4091 as in 4.3; no GUID
  comparison, no factory reset, no deleting of files, no second try.
  - \+ openExpress never deletes on a device; the owner of the other account decides.
- **5b. As Garmin Express: offer a factory reset and try again.**
  - − A destructive step on a device that may be someone else's.

### D6: a device Garmin keeps as retired — chosen: 6a

- **6a. Made active, as Garmin Express does, within the same confirmation (recommended;
  chosen):** the answer says `retired`: one `PUT …/device/status/{unitId}`
  `{"deviceStatus":"active"}`; the confirmation dialog said "as an active device", so no second
  one. The log says it.
  - \+ A device the account kept from before (and may not list while retired) becomes usable.
- **6b. Said, left to Garmin Connect.**
  - − The device stays unusable in openExpress until the user finds the setting there.

### D7: what says it worked — chosen: 7a

- **7a. The list asked anew (recommended; chosen):** before the request (already on it: nothing sent)
  and after it; the answer's `unitId`, when it gives one, must be the device's (else an error,
  nothing more sent). The device's check is then run as after Refresh.
  - \+ The same test that refused the device decides that it may be used.

### D8: where the window offers it — chosen: 8a

- **8a. The banner's button and a row of the Device page (recommended; chosen):** 4.1.
  - \+ Where the refusal is said, and where the device's account is described.
- **8b. The banner only.**
  - − Gone once the banner is closed.

### D9: the command's name — chosen: 9a

- **9a. `openexpress register [--apply]` (recommended; chosen):** the word of the message that refuses
  ("not registered to your Garmin account").
- **9b. `openexpress device add`:** a command `device` next to `devices`, which lists the devices
  this installation has seen: two commands one letter apart.
- **9c. `openexpress account add`:** a new command `account` for one action.

### D10: the rest of Garmin Express's chain — chosen: 10a

- **10a. Not done (recommended; chosen):** no profile, e-mail, phone, nickname, Wi-Fi or time zone written,
  no preferred activity tracker set; after adding, the row "Primary Training Device" offers Set
  as Primary as for any device of the account.
  - \+ Each of them is a change of its own, on the device or the account, with its own
    confirmation dialog where openExpress has it already.

## 6. What can go wrong, and how it is found

| What | How it is prevented, and found |
|---|---|
| A device added to the wrong account | the confirmation dialog names the account; the advice about the phone's app; Garmin refuses a device of another account (409) |
| Someone else's device added | it must be plugged in and the addition confirmed; on another account Garmin refuses |
| The device's credentials leak | the body never kept nor logged; a test that a made-up answer's credentials appear in no log, error or file |
| Two requests | one request a click, the button busy and no other task meanwhile; never repeated by itself |
| Garmin accepts, the list lacks it | said (4.3); the list asked again on Refresh |
| Retired | made active (D6); a test with a made-up answer |
| An answer naming another device | an error, nothing more sent |
| No network, or no answer | not known whether it was added: the list on Refresh says |
| The China environment | the request goes to the host of the environment set (`setup --environment`), as every request |
| The tests | no network: the request on a recorded session only |
