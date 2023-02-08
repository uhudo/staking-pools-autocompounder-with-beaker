from algosdk import account, mnemonic
from algosdk.atomic_transaction_composer import AccountTransactionSigner
from pyteal import *
from beaker import *
from typing import Final


# Create a class, subclassing Application from beaker
class Autocompounder(Application):

    # ----- -----    Constants     ----- -----
    LAST_COMPOUND_NOT_DONE = 0
    LAST_COMPOUND_DONE = 1

    # QM.N
    LOCAL_STAKE_M = 8
    LOCAL_STAKE_N = 8
    LOCAL_STAKE_SIZE = LOCAL_STAKE_M + LOCAL_STAKE_N
    LOCAL_STAKE_ZERO_BYTES = BytesZero(Int(LOCAL_STAKE_SIZE))

    # Number of bytes needed for the box name
    BOX_NAME_SIZE = 8
    # Maximum number of bytes needed for the box
    BOX_MAX_SIZE = LOCAL_STAKE_SIZE

    MIN_TX_FEE = 1_000
    STAKE_TO_SC_FEE = 3 * MIN_TX_FEE
    UNSTAKE_FROM_SC_FEE = 3 * MIN_TX_FEE
    CLAIM_FROM_SC_FEE = 4 * MIN_TX_FEE
    BOX_FEE = 2_500 + 400 * (BOX_NAME_SIZE + BOX_MAX_SIZE)
    ZAP_FEE = 4 * MIN_TX_FEE

    PAY_FEE = 1
    DO_NOT_PAY_FEE = 0

    # Fees for one trigger = fee for box + fee for claiming from SC + fee for staking to SC
    CC_FEE_FOR_COMPOUND = BOX_FEE + CLAIM_FROM_SC_FEE + STAKE_TO_SC_FEE
    # ----- -----                     ----- -----

    # ----- -----    Global state     ----- -----
    CC_total_stake: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("TS"),
        descr="Total Stake: deposited by all users (accumulated through compounding)",
    )

    CC_pool_end_round: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("PER"),
        descr="Pool End Round: round number of when the staking pool ends",
    )

    CC_pool_start_round: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("PSR"),
        descr="Pool Start Round: round number of when the staking pool starts",
    )

    CC_last_compound_done: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("LCD"),
        descr="Last Compound Done: set to LAST_COMPOUND_DONE when the pool has been compounded after the pool has ended",
    )

    CC_last_compound_round: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("LCR"),
        descr="Last Compound Round: round number of when the stake has been last compounded",
    )

    CC_number_of_stakers: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("NS"),
        descr="Number of Stakers",
    )

    CC_claiming_period: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("CP"),
        descr="Claiming Period: number of rounds after the pool has ended that creator has to wait before the contract can be deleted",
    )

    CC_number_of_boxes: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("NB"),
        descr="Number of Boxes: number of boxes created by the contract",
    )

    CC_SC_ID: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("SC_ID"),
        descr="Staking Contract ID: ID of the staking pool to compound",
    )

    CC_AC_ID: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("AC_ID"),
        descr="Associated Contract ID: app ID with which SC interacts",
    )

    CC_S_ASA_ID: Final[ApplicationStateValue] = ApplicationStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("S_ASA_ID"),
        descr="S_ASA ID: ID of the staking asset",
    )

    # ----- -----    Local state     ----- -----
    CC_local_number_of_boxes: Final[AccountStateValue] = AccountStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        key=Bytes("LNB"),
        descr="Local Number of Boxes: Number of box when the user has last compounded their rewards",
    )

    CC_local_stake: Final[AccountStateValue] = AccountStateValue(
        stack_type=TealType.bytes,
        key=Bytes("LS"),
        descr="Local Stake: amount staked by the users (accumulated through compounding) - a fractional number!",
    )

    # ----- -----                     ----- -----

    SC_address = AppParam.address(CC_SC_ID)

    # Calculation of next round to compound
    number_of_triggers = (Balance(Global.current_application_address()) - MinBalance(
        Global.current_application_address())) / Int(CC_FEE_FOR_COMPOUND)
    next_compound_round = (CC_pool_end_round - CC_last_compound_round) / number_of_triggers + CC_last_compound_round

    # ----- -----    Internal methods     ----- -----

    # floor_local_stake() -> Expr:
    # Get rounded down integer amount of user's local stake
    #
    @internal(TealType.uint64)
    def floor_local_stake(self) -> Expr:
        # Variable for storing the local stake
        ls = ScratchVar()

        return Seq(
            ls.store(self.CC_local_stake),
            If(
                Len(ls.load()) > Int(self.LOCAL_STAKE_N)
            ).Then(
                Btoi(Extract(ls.load(), Int(0), Len(ls.load()) - Int(self.LOCAL_STAKE_N)))
            ).Else(
                Int(0)
            )
        )

    # closeAccountTo(account: Expr) -> Expr:
    #  Sends remaining balance of the application account to a specified account, i.e. it closes the application account.
    #  Fee for the inner transaction is set to zero, thus fee pooling needs to be used.
    #
    @internal(TealType.none)
    def closeAccountTo(self, account: Expr) -> Expr:
        return If(Balance(Global.current_application_address()) != Int(0)).Then(
            Seq(
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                    {
                        TxnField.fee: Int(0),
                        TxnField.type_enum: TxnType.Payment,
                        TxnField.close_remainder_to: account,
                    }
                ),
                InnerTxnBuilder.Submit(),
            )
        )

    # payTo(account: Expr, amount: Expr) -> Expr:
    #  Sends a payment transaction of amount to account
    #
    @internal(TealType.none)
    def payTo(self, account: Expr, amount: Expr) -> Expr:
        return Seq(
            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.SetFields(
                {
                    TxnField.fee: Int(0),
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver: account,
                    TxnField.amount: amount
                }
            ),
            InnerTxnBuilder.Submit(),
        )

    # closeAssetToCreator() -> Expr:
    #  Sends whole amount of S_ASA_ID to CC creator
    #
    @internal(TealType.none)
    def closeAssetToCreator(self) -> Expr:
        return Seq(
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.AssetTransfer,
                    TxnField.xfer_asset: self.CC_S_ASA_ID,
                    TxnField.asset_close_to: Global.creator_address(),
                    TxnField.fee: Int(0),
                }
            )
        )

    # stake_to_SC(amt: Expr, payFee: Expr) -> Expr:
    #  Issue app call to SC to stake additional amount amt
    #  If payFee == PAY_FEE, CC.address will pay the fee for the staking operation. Otherwise, the fee needs to be
    #  pooled.
    #
    @internal(TealType.none)
    def stake_to_SC(self, amt: Expr, payFee: Expr) -> Expr:
        return Seq(
            # Assert address of SC
            self.SC_address,
            Assert(self.SC_address.hasValue()),
            # Stake to SC
            InnerTxnBuilder.Begin(),
            #  First create an asset transfer transaction
            InnerTxnBuilder.SetFields(
                {
                    TxnField.type_enum: TxnType.AssetTransfer,
                    TxnField.xfer_asset: self.CC_S_ASA_ID,
                    TxnField.asset_receiver: self.SC_address.value(),
                    TxnField.asset_amount: amt,
                    TxnField.fee: Int(0),
                }
            ),
            InnerTxnBuilder.Next(),
            #  Then create an app call to stake
            InnerTxnBuilder.SetFields(
                {
                    TxnField.type_enum: TxnType.ApplicationCall,
                    TxnField.application_id: self.CC_SC_ID,
                    TxnField.applications: [self.CC_AC_ID],
                    TxnField.assets: [self.CC_S_ASA_ID],
                    TxnField.accounts: [self.SC_address.value()],
                    TxnField.on_completion: OnComplete.NoOp,
                    TxnField.application_args: [
                        Bytes("base64", "AA=="),
                        Bytes("base64", "Aw=="),
                        Bytes("base64", "AAAAAAAAAAA="),
                        Concat(Bytes("base16", "0x02"), Itob(amt)),
                    ],
                    TxnField.fee: If(
                        payFee == Int(self.PAY_FEE),
                    ).Then(
                        Int(self.STAKE_TO_SC_FEE),
                    ).Else(
                        Int(0),
                    )
                }
            ),
            #  Submit the created group transaction
            InnerTxnBuilder.Submit(),
        )

    # claim_stake_record(amt: Expr, payFee: Expr) -> Expr:
    #  First, claim rewards from SC. Then create the recording of the claimed amount by storing it in a newly created
    #  box.
    #  Lastly, stake the claimed amount plus any additional amount to SC, and update the total stake as well as the last
    #  compound round.
    #  If payFee == PAY_FEE, CC.address will pay the fee for the operations. Otherwise, the fee needs to be pooled.
    #
    @internal(TealType.none)
    def claim_stake_record(self, amt: Expr, payFee: Expr) -> Expr:
        # Variable for storing the claimed amount
        claim_amt = ScratchVar()

        # Variable for storing the amount to stake
        stake_amt = ScratchVar()

        # Boxes are sequentially numbered
        box_name = Itob(self.CC_number_of_boxes)

        # Amount of increase: 1 + (claim_amt / total stake)
        increase = BytesAdd(
            Concat(Itob(Int(1)), BytesZero(Int(self.LOCAL_STAKE_N))),
            BytesDiv(
                Concat(Itob(claim_amt.load()), BytesZero(Int(self.LOCAL_STAKE_N))),
                Concat(BytesZero(Int(self.LOCAL_STAKE_N)), Itob(self.CC_total_stake))
            )
        )

        return Seq(
            # Claiming makes sense only if current total stake was non-zero
            Assert(self.CC_total_stake > Int(0)),
            # Assert address of SC
            self.SC_address,
            Assert(self.SC_address.hasValue()),
            # Claim from SC
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.ApplicationCall,
                    TxnField.application_id: self.CC_SC_ID,
                    TxnField.applications: [self.CC_AC_ID],
                    TxnField.assets: [self.CC_S_ASA_ID],
                    TxnField.accounts: [self.SC_address.value()],
                    TxnField.on_completion: OnComplete.NoOp,
                    TxnField.application_args: [
                        Bytes("base64", "AA=="),
                        Bytes("base64", "Aw=="),
                        Bytes("base64", "AAAAAAAAAAA="),
                        Bytes("base64", "AAAAAAAAAAAA"),
                    ],
                    TxnField.fee: If(
                        payFee == Int(self.PAY_FEE)
                    ).Then(
                        Int(self.CLAIM_FROM_SC_FEE),
                    ).Else(
                        Int(0),
                    )
                }
            ),

            # Store the claimed amount, which is written in the last log of the call claim from SC, 8 bytes starting
            # from byte 16
            claim_amt.store(Btoi(Extract(InnerTxn.last_log(), Int(16), Int(8)))),

            # Increase the counter of boxes created
            self.CC_number_of_boxes.set(self.CC_number_of_boxes + Int(1)),

            # Create a new box with name equal to the number of boxes and populate it with the increase from this
            # compounding
            App.box_put(box_name, increase),

            # Stake the claimed amount plus any additional stake - if they are non-zero
            stake_amt.store(claim_amt.load() + amt),
            If(stake_amt.load() > Int(0)).Then(
                self.stake_to_SC(stake_amt.load(), payFee),

                # Update the total stake in CC
                self.CC_total_stake.set(self.CC_total_stake + stake_amt.load()),
            ),

            # Update the round of last compound to current round
            self.CC_last_compound_round.set(Global.round())
        )

    # unstake_from_SC(amt: Expr, payFee: Expr) -> Expr:
    #  Issue app call to SC to unstake amount amt
    #  If payFee == PAY_FEE, CC.address will pay the fee for the unstaking operation. Otherwise, the fee needs to be pooled.
    #
    @internal(TealType.none)
    def unstake_from_SC(self, amt: Expr, payFee: Expr) -> Expr:
        return Seq(
            # Assert address of SC
            self.SC_address,
            Assert(self.SC_address.hasValue()),
            # Unstake from SC
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.ApplicationCall,
                    TxnField.application_id: self.CC_SC_ID,
                    TxnField.applications: [self.CC_AC_ID],
                    TxnField.assets: [self.CC_S_ASA_ID],
                    TxnField.accounts: [self.SC_address.value()],
                    TxnField.on_completion: OnComplete.NoOp,
                    TxnField.application_args: [
                        Bytes("base64", "AA=="),
                        Bytes("base64", "Aw=="),
                        Bytes("base64", "AAAAAAAAAAA="),
                        Concat(Bytes("base16", "0x03"), Itob(amt)),
                    ],
                    TxnField.fee: If(
                        payFee == Int(self.PAY_FEE)
                    ).Then(
                        Int(self.UNSTAKE_FROM_SC_FEE),
                    ).Else(
                        Int(0),
                    )
                }
            ),
        )

    # sendAssetToSender(amt: Expr) -> Expr:
    #  Sends amount amt of S_ASA_ID to Txn.sender()
    #
    @internal(TealType.none)
    def sendAssetToSender(self, amt: Expr) -> Expr:
        return Seq(
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.AssetTransfer,
                    TxnField.xfer_asset: self.CC_S_ASA_ID,
                    TxnField.asset_amount: amt,
                    TxnField.asset_receiver: Txn.sender(),
                    TxnField.fee: Int(0),
                }
            )
        )

    # local_claim_box(box_i: Expr) -> Expr:
    #  Adds amount received by user from a compounding that was done and recorded in box
    #
    @internal(TealType.none)
    def local_claim_box(self, box_int: Expr) -> Expr:
        # Box name = sequential number of the box (uint64)
        box_name = Itob(box_int)

        # Box contents
        contents = App.box_get(box_name)
        # Get increase from the box
        increase = contents.value()

        return Seq(
            # Assert the box already exists
            contents,
            Assert(contents.hasValue()),
            # Claims must be done in a strictly increasing order, without skipping any claim.
            # This is checked by asserting the local number of boxes is one smaller than the current box_int
            # This is needed to be able to track what has already been (locally) claimed and what not, without having to
            # record this info explicitly for each user.
            Assert(box_int == self.CC_local_number_of_boxes + Int(1)),

            # Increase local stake for the contribution of the user to the time the compounding has been done,
            # i.e. += (box[round].claimed * LS) / box[round].total_stake_at_time_of_compounding, which equals
            # *= box[round].increase
            self.CC_local_stake.set(
                BytesDiv(
                    BytesMul(self.CC_local_stake, increase),
                    Concat(Itob(Int(1)), BytesZero(Int(self.LOCAL_STAKE_N)))
                )
             ),

            # Update local number of boxes
            self.CC_local_number_of_boxes.set(box_int)
        )

    # ----- -----                         ----- -----

    # ----- -----    External methods     ----- -----
    @delete(authorize=Authorize.only(Global.creator_address()))
    def delete(self):
        return Seq(
            # Only when there are no more accounts opted into the CC and the pool has ended, or the claiming period has
            # passed
            Assert(
                Or(
                    And(self.CC_number_of_stakers == Int(0), Global.round() > self.CC_pool_end_round),
                    Global.round() > (self.CC_pool_end_round + self.CC_claiming_period)
                )
            ),
            # Only when all boxes were deleted
            Assert(self.CC_number_of_boxes == Int(0)),
            # Ensure all funds have either already been claimed from SC to CC or do it now
            If(self.CC_last_compound_done == Int(self.LAST_COMPOUND_NOT_DONE)).Then(
                Seq(
                    # Make a claim to SC
                    # Assert address of SC
                    self.SC_address,
                    Assert(self.SC_address.hasValue()),
                    # Claim from SC - fees should be pooled
                    InnerTxnBuilder.Execute(
                        {
                            TxnField.type_enum: TxnType.ApplicationCall,
                            TxnField.application_id: self.CC_SC_ID,
                            TxnField.applications: [self.CC_AC_ID],
                            TxnField.assets: [self.CC_S_ASA_ID],
                            TxnField.accounts: [self.SC_address.value()],
                            TxnField.on_completion: OnComplete.NoOp,
                            TxnField.application_args: [
                                Bytes("base64", "AA=="),
                                Bytes("base64", "Aw=="),
                                Bytes("base64", "AAAAAAAAAAA="),
                                Bytes("base64", "AAAAAAAAAAAA"),
                            ],
                            TxnField.fee: Int(0),
                        }
                    ),
                    # Unstake total stake from SC - fees should be pooled
                    self.unstake_from_SC(self.CC_total_stake, Int(self.DO_NOT_PAY_FEE)),
                )
            ),

            # Close all S_ASA_ID to the CC creator
            self.closeAssetToCreator(),

            # Clear state of CC in SC
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.ApplicationCall,
                    TxnField.on_completion: OnComplete.ClearState,
                    TxnField.application_id: self.CC_SC_ID,
                    TxnField.fee: Int(0),
                }
            ),

            # Close the contract account to the CC creator
            self.closeAccountTo(Global.creator_address()),
            Approve(),
        )

    @close_out
    def close_out(self):
        return Seq(
            # Allow opting out only if user has withdrawn all (integer part) of the stake - otherwise funds would be
            # lost
            Assert(self.floor_local_stake() == Int(0)),
            # Reduce number of opted-in accounts
            self.CC_number_of_stakers.set(self.CC_number_of_stakers - Int(1)),
            Approve()
        )

    @opt_in
    def opt_in(self):
        return Seq(
            # Opt-ins are allowed only until the pool is live
            Assert(self.CC_pool_end_round > Global.round()),
            # Opt-ins are allowed only if the contract has already been setup - which is reflected in last compound
            # round
            Assert(self.CC_last_compound_round > Int(0)),
            # Initialize local state
            self.CC_local_number_of_boxes.set(self.CC_number_of_boxes),
            self.CC_local_stake.set(self.LOCAL_STAKE_ZERO_BYTES),
            # Increase the number of opted-in accounts
            self.CC_number_of_stakers.set(self.CC_number_of_stakers + Int(1)),
            Approve()
        )

    # @clear_state
    # def clear_state(self):
    #     return Seq(
    #         # Note: User will forfeit their stake
    #         # Reduce number of opted-in accounts
    #         self.CC_number_of_stakers.set(f.CC_number_of_stakers - Int(1)),
    #         Approve()
    #     )

    @create
    def create(self, SC_ID: abi.Uint64, AC_ID: abi.Uint64, claimPeriod: abi.Uint64):
        # Get global state of SC at key value of 0x00
        SC_glob_state = App.globalGetEx(self.CC_SC_ID, Bytes("base64", "AA=="))

        return Seq(
            # Set global variables
            self.CC_SC_ID.set(SC_ID.get()),
            self.CC_AC_ID.set(AC_ID.get()),
            self.CC_claiming_period.set(claimPeriod.get()),

            # Fetch start round for the pool from the SC
            #  Assert SC has a global state
            SC_glob_state,
            Assert(SC_glob_state.hasValue()),
            #  Assign 8 bytes starting at byte 56 as start round
            self.CC_pool_start_round.set(Btoi(Extract(SC_glob_state.value(), Int(56), Int(8)))),
            # Fetch end round for the pool from the SC
            #  Assign 8 bytes starting at byte 64 as end round
            self.CC_pool_end_round.set(Btoi(Extract(SC_glob_state.value(), Int(64), Int(8)))),
            # Fetch asset of the pool from the SC
            #  Assign 8 bytes starting at byte 48 as ASA ID
            self.CC_S_ASA_ID.set(Btoi(Extract(SC_glob_state.value(), Int(48), Int(8)))),

            # Initialize remaining global variables
            self.CC_total_stake.set(Int(0)),
            self.CC_last_compound_done.set(Int(self.LAST_COMPOUND_NOT_DONE)),
            self.CC_last_compound_round.set(Int(0)),
            self.CC_number_of_stakers.set(Int(0)),
            self.CC_number_of_boxes.set(Int(0)),

            Approve()
        )

    @external(authorize=Authorize.only(Global.creator_address()))
    def on_setup(self):
        return Seq(
            # Assert last compounded round is zero - only at the start (i.e. setup can be done only once)
            Assert(self.CC_last_compound_round == Int(0)),

            # Assign start of pool as the last time compounding took place, thus it can't be done before it's meaningful
            self.CC_last_compound_round.set(self.CC_pool_start_round),

            # Opt-in to S_ASA_ID
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.AssetTransfer,
                    TxnField.xfer_asset: self.CC_S_ASA_ID,
                    TxnField.asset_receiver: Global.current_application_address(),
                    TxnField.fee: Int(0),
                }
            ),

            # Opt-in to SC_ID
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.ApplicationCall,
                    TxnField.on_completion: OnComplete.OptIn,
                    TxnField.application_id: self.CC_SC_ID,
                    TxnField.fee: Int(0),
                }
            ),

            # Approve the call
            Approve(),
        )

    @external
    def trigger_compound(self):
        return Seq(
            # Compounding can be done only if enough time has passed since last compounding
            Assert(self.next_compound_round <= Global.round(), comment="Trigger compounding"),
            # Compounding does not make sense if the pool is not yet live - prevents also box creation prior to PSR
            Assert(Global.round() > self.CC_pool_start_round, comment="Trigger compounding - pool live"),
            # Claim from SC and record the claiming in a box, without adding any additional stake. The fee is paid by CC
            self.claim_stake_record(Int(0), Int(self.PAY_FEE)),
            # Approve the call
            Approve(),
        )

    @external
    def stake(self):
        # The request to stake must be accompanied by a payment transaction to deposit funds to cover the fees for at
        # least one compounding
        pay_txn_idx = Txn.group_index() - Int(2)
        # Amount for payment of compounding fees
        amt = Gtxn[pay_txn_idx].amount()

        # The request to stake must be accompanied by a transaction transferring the amount of S_ASA_ID to be staked
        xfer_txn_idx = Txn.group_index() - Int(1)
        # Amount of S_ASA_ID transferred to be staked
        amt_xfer = Gtxn[xfer_txn_idx].asset_amount()

        return Seq(
            # Deposits are allowed only if pool is still live
            Assert(Global.round() < self.CC_pool_end_round),

            # Staking is allowed only if user has either:
            #  claimed all compounding contributions (otherwise local contributions would not be correctly reflected) or
            #  user has currently a zero local stake, in which case the local number of boxes must be updated
            If(self.CC_local_number_of_boxes == self.CC_number_of_boxes).Then(
                Assert(Int(1), comment="boxes up-to-date, user can stake")
            ).Else(
                If(BytesEq(self.CC_local_stake, self.LOCAL_STAKE_ZERO_BYTES)).Then(
                    Seq(
                        self.CC_local_number_of_boxes.set(self.CC_number_of_boxes),
                        Assert(Int(1), comment="user can stake since it has zero stake")
                    )
                ).Else(
                    Reject()
                )
            ),

            # The request to stake must be accompanied by a payment transaction to deposit funds to cover the fees for
            # at least one compounding
            #  Assert transaction is payment
            Assert(Gtxn[pay_txn_idx].type_enum() == TxnType.Payment),
            #  Assert transaction receiver is CC.address
            Assert(Gtxn[pay_txn_idx].receiver() == Global.current_application_address()),

            # The request to stake must be accompanied by a transaction transferring the amount to be staked
            #  Assert transaction is asset transfer
            Assert(Gtxn[xfer_txn_idx].type_enum() == TxnType.AssetTransfer),
            #  Assert transaction receiver is CC.address
            Assert(Gtxn[xfer_txn_idx].asset_receiver() == Global.current_application_address()),
            #  Assert transaction is transferring correct asset (redundant since opted-in only one asset, thus others
            #  would fail)
            Assert(Gtxn[xfer_txn_idx].xfer_asset() == self.CC_S_ASA_ID),

            # If request to stake is done when the pool is already live and a stake has already been deposited, it is
            # necessary to claim the amount first. For this claiming, the new staker needs to pay the fees, including
            # additional deposit for the newly created box while claiming.
            If(
                And(
                    Global.round() > self.CC_pool_start_round,
                    self.CC_total_stake > Int(0)
                )
            ).Then(
                Seq(
                    # Assert the payment transferred is enough to cover the fees for this initial compounding due to
                    # staking plus for another trigger (at any later point)
                    Assert(amt >= Int(2 * self.CC_FEE_FOR_COMPOUND),
                           comment="staking while pool live, stake already deposited"),
                    # Claim from SC and record the claiming in a box, while adding the newly deposited additional stake.
                    # Everything also get recorded in total stake.
                    self.claim_stake_record(amt_xfer, Int(self.PAY_FEE)),

                    # Local claim the results of this compounding (it is still with respect to user's old stake)
                    self.local_claim_box(self.CC_number_of_boxes),
                )
            ).Else(
                Seq(
                    # Assert the payment transferred is enough to cover the fees for the transfer to SC plus for a
                    # compound trigger (at any later point)
                    Assert(amt >= Int(self.CC_FEE_FOR_COMPOUND + self.STAKE_TO_SC_FEE)),
                    # Stake the deposited amount
                    self.stake_to_SC(amt_xfer, Int(self.PAY_FEE)),
                    # Update the total stake
                    self.CC_total_stake.set(self.CC_total_stake + amt_xfer),
                )
            ),

            # Update the local stake
            self.CC_local_stake.set(
                BytesAdd(
                    self.CC_local_stake,
                    Concat(Itob(amt_xfer), BytesZero(Int(self.LOCAL_STAKE_N))),
                )
            ),

            # Approve the call
            Approve(),
        )

    @external
    def compound_now(self):
        # To compound now, a payment transaction needs to deposit funds to cover the fees for the compounding
        pay_txn_idx = Txn.group_index() - Int(1)
        # Amount for payment of compounding fees
        amt = Gtxn[pay_txn_idx].amount()

        return Seq(
            # Makes sense to allow additional compounding only when the pool is live
            Assert(Global.round() < self.CC_pool_end_round),
            Assert(Global.round() > self.CC_pool_start_round),

            # The request to stake must be accompanied by a payment transaction to deposit funds to cover the fees for
            # the compounding
            #  Assert transaction is payment
            Assert(Gtxn[pay_txn_idx].type_enum() == TxnType.Payment),
            #  Assert transaction receiver is CC.address
            Assert(Gtxn[pay_txn_idx].receiver() == Global.current_application_address()),
            # Assert the payment transferred is enough to cover the fees for the compounding
            Assert(amt >= Int(self.CC_FEE_FOR_COMPOUND)),

            # Claim from SC and record the claiming in a box (do not add additional stake).
            self.claim_stake_record(Int(0), Int(self.PAY_FEE)),

            # Approve the call
            Approve(),
        )

    @external
    def withdraw(self, amt: abi.Uint64, *, output: abi.Uint64) -> Expr:
        # The request to unstake must be accompanied by a payment transaction to deposit funds to cover the fees
        #  This could be optimized depending on when the unstaking is done, fees can be simply pooled.
        pay_txn_idx = Txn.group_index() - Int(1)
        # Amount for payment of fees
        amt_fee = Gtxn[pay_txn_idx].amount()

        # For storing user's local state at the start of the call (since it can increase due to claiming)
        local_stake_b = ScratchVar()
        # For storing amount which user actually gets withdraw - which can be higher than amt if another claiming has to
        # be done
        amt_b = ScratchVar()

        return Seq(
            # Withdrawals are allowed only if user has claimed all compounding contributions - otherwise one could lose
            # funds (i.e. give up the rewards)
            Assert(self.CC_local_number_of_boxes == self.CC_number_of_boxes),

            # The request to unstake must be accompanied by a payment transaction to deposit funds to cover the fees
            #  Assert transaction is payment
            Assert(Gtxn[pay_txn_idx].type_enum() == TxnType.Payment),
            #  Assert transaction receiver is CC.address
            Assert(Gtxn[pay_txn_idx].receiver() == Global.current_application_address()),

            # Get user's local state (rounded down) - since it can increase due to claiming later on
            local_stake_b.store(self.floor_local_stake()),

            # Requested withdrawal can be at most up to the local stake
            Assert(amt.get() <= local_stake_b.load()),

            # Withdrawals are processed differently depending on when they are done
            If(Global.round() < self.CC_pool_start_round).Then(
                Seq(
                    # If pool has not yet started, there has been no compounding done so far, thus simply ustake the
                    # requested amount from SC
                    self.unstake_from_SC(amt.get(), Int(self.PAY_FEE)),
                    # That amount will be withdrawn
                    amt_b.store(amt.get()),
                    # Assert fees for the unstaking have been deposited
                    Assert(amt_fee >= Int(self.UNSTAKE_FROM_SC_FEE)),
                )
            ).Else(
                # If pool is still live
                If(Global.round() <= self.CC_pool_end_round).Then(
                    Seq(
                        # Claim from SC and record the claiming in a box, without adding any additional stake.
                        # Everything also get recorded in total stake.
                        self.claim_stake_record(Int(0), Int(self.PAY_FEE)),

                        # Local claim the results of this compounding
                        self.local_claim_box(self.CC_number_of_boxes),

                        # Unstake correct amount from SC
                        #  If withdraw amt equaled the whole local stake, interpret as a request to withdraw also the
                        #  effect of the last claim - which requires to call floor_local_stake() again for the unstaking
                        If(amt.get() == local_stake_b.load()).Then(
                            amt_b.store(self.floor_local_stake()),
                        ).Else(
                            amt_b.store(amt.get()),
                        ),
                        self.unstake_from_SC(amt_b.load(), Int(self.PAY_FEE)),

                        # Assert fees for the compounding and unstaking have been deposited
                        Assert(amt_fee >= Int(self.CC_FEE_FOR_COMPOUND + self.UNSTAKE_FROM_SC_FEE)),
                    )
                ).Else(
                    # If pool has already ended, a last compounding has to be done and all funds can be withdrawn from
                    # the pool to CC.address
                    If(self.CC_last_compound_done == Int(self.LAST_COMPOUND_NOT_DONE)).Then(
                        Seq(
                            # Claim from SC and record the claiming in a box, without adding any additional stake.
                            # Everything also get recorded in total stake.
                            self.claim_stake_record(Int(0), Int(self.PAY_FEE)),

                            # Local claim the results of this compounding
                            self.local_claim_box(self.CC_number_of_boxes),
                            #  If withdraw amt equaled the whole local stake, interpret as a request to withdraw also
                            #  the effect of the last claim - which requires to call floor_local_stake() again
                            If(amt.get() == local_stake_b.load()).Then(
                                amt_b.store(self.floor_local_stake()),
                            ).Else(
                                amt_b.store(amt.get()),
                            ),

                            # Unstake total stake from SC
                            self.unstake_from_SC(self.CC_total_stake, Int(self.PAY_FEE)),

                            # Assert fees for the compounding and unstaking have been deposited
                            Assert(amt_fee >= Int(self.CC_FEE_FOR_COMPOUND + self.UNSTAKE_FROM_SC_FEE)),

                            # Mark that the last compounding has now been done
                            self.CC_last_compound_done.set(Int(self.LAST_COMPOUND_DONE)),
                        )
                    )
                        .Else(
                        # If pool has already ended and a last compounding has already been done, stake can simply be
                        # withdrawn from CC.address since it’s already there.
                        # The amount to withdraw is simply the requested stake since no additional claiming happened
                        amt_b.store(amt.get())
                    )
                )
            ),

            # Send the requested amount (in case of a full withdrawal, also the possible last compounding) to the user.
            # Fees for this action are pooled by the user.
            self.sendAssetToSender(amt_b.load()),

            # Record the new total stake
            self.CC_total_stake.set(self.CC_total_stake - amt_b.load()),

            # Record that the new local stake for the user
            self.CC_local_stake.set(BytesMinus(
                    self.CC_local_stake,
                    Concat(Itob(amt_b.load()), BytesZero(Int(self.LOCAL_STAKE_N)))
                )
            ),

            # Output the withdrawn amount
            output.set(amt_b.load()),

            # Approve the call
            # Approve(), - leaving this in prevents outputting the results from the method because Approve() returns
            # sooner than the output.set returns
        )

    @external
    def local_claim(self, up_to_box: abi.Uint64):
        # Make a local claim of the compounding contribution for each box from last claimed one (i.e.
        # local_number_of_boxes) up to (including) up_to_box.
        # All the boxes need to be provided in the box array call.

        idx = ScratchVar()
        init = idx.store(self.CC_local_number_of_boxes + Int(1))
        cond = idx.load() <= up_to_box.get()
        iter = idx.store(idx.load() + Int(1))

        return Seq(
            # Process each supplied box
            For(init, cond, iter).Do(
                self.local_claim_box(idx.load())
            ),

            # Approve the call
            Approve(),
        )

    @external
    def delete_boxes(self, down_to_box: abi.Uint64):
        # Delete each box from (including) number_of_boxes down to (excluding) down_to_box
        # All need to be supplied in the box array

        idx = ScratchVar()
        init = idx.store(self.CC_number_of_boxes)
        cond = idx.load() > down_to_box.get()
        iter = idx.store(idx.load() - Int(1))

        return Seq(
            # Only app creator can delete boxes
            Assert(Txn.sender() == Global.creator_address()),
            # Boxes can be deleted only if there are no more accounts opted into the CC and the pool has ended, or the
            # claiming period has passed
            Assert(Or(
                And(self.CC_number_of_stakers == Int(0), Global.round() > self.CC_pool_end_round),
                Global.round() > self.CC_pool_end_round + self.CC_claiming_period
            )
            ),

            # Delete each supplied box
            For(init, cond, iter).Do(
                Assert(App.box_delete(Itob(idx.load()))),
            ),

            # Update new number of boxes
            self.CC_number_of_boxes.set(idx.load()),

            # Approve the call
            Approve(),
        )


def deploy(user_sk, sc_id, ac_id, cp):

    # Create an Application client
    app_client = client.ApplicationClient(
        client=client.AlgoExplorer(client.Network.TestNet).algod(),
        app=Autocompounder(version=8),
        signer=AccountTransactionSigner(user_sk),
    )

    # Deploy the app on-chain
    app_id, app_addr, txid = app_client.create(
        SC_ID=sc_id, AC_ID=ac_id, claimPeriod=cp, foreign_apps=[sc_id]
    )

    return [app_id, txid]
