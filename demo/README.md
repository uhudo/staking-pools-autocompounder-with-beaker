# Video Demo

The video excerpts below demonstrate an example scenario of interactions of two accounts `a` and `b` with the 
autocompounder for a distribution pool.
The interactions are made with the provided interaction [script](../interactions_state_machine.py) and take place on 
Algorand Testnet.

Before the start of interactions with the autocompounder, a distribution pool is first created on 
[Cometa](https://app.testnet.cometa.farm/farm) for distribution of 
[test USDC](https://testnet.algoexplorer.io/asset/10458941).

![Video of distribution pool creation on Cometa](https://user-images.githubusercontent.com/115161770/217392889-129b3a87-a9a6-4aba-90ca-22fe819380d9.mp4)

Account `a` creates the autocompounder, sets its parameters and sets up the contract 
(deployed App ID [157631943](https://testnet.algoexplorer.io/application/157631943)).

![Video of user (a) creating and setting up the autocompounder contract](https://user-images.githubusercontent.com/115161770/217392898-fd2c8bab-a589-4f4b-83c0-f99fef72046d.mp4)

Afterwards, account `a` switches to normal user interaction mode, opts into the contract, and deposits staking tokens 
as well as funds for one compounding.

![Video of user (a) opt-ing into the contract and staking](https://user-images.githubusercontent.com/115161770/217392914-b261a09f-5548-4f60-80be-653e033e2090.mp4)

When the pool is already live, account `b` opts into the autocompounder and stakes its tokens.
Since the pool is now live, the stake is first compounded.

![Video of user (b) opt-ing into the contract and staking](https://user-images.githubusercontent.com/115161770/217392925-66865917-3b46-4227-8eb5-52dc9373a721.mp4)

Account `b` withdraws some tokens.
Again, the stake first needs to be compounded.
This action results in a rescheduling of the autocompounder's schedule.

![Video of user (b) withdrawing some tokens](https://user-images.githubusercontent.com/115161770/217392932-fbcc3158-d03c-4fa1-a8d2-c0c60ad61b59.mp4)

It then triggers the compounding according to the schedule.
Afterwards, it issues an instant additional compounding of the stake.

![Video of user (b) triggeting a scheduled compounding and additional instant compounding](https://user-images.githubusercontent.com/115161770/217393055-0667f61a-395f-4923-8927-a91de043cb95.mp4)

The pool ends.
Account `b` withdraws all its stake and opts out of the contract.
Account `a` connects and does the same.

![Video of users (b) and (a) withdrawing funds after the pool ends](https://user-images.githubusercontent.com/115161770/217393069-a1b5f950-6a05-4fb9-9192-bc984e5f3645.mp4)

Account `a` deletes the boxes that were created.
Afterwards, it deletes the contract, receiving the remaining funds from it.

![Video of user (a) deleting the boxes and contract after the pool ends](https://user-images.githubusercontent.com/115161770/217393080-06dfeac7-f158-4e1b-9ccd-6cefd93ba34f.mp4)
